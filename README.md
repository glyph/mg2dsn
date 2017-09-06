# What is this?

At twistedmatrix.com, we use [mailgun](https://www.mailgun.com) to route our
inbound email to a variety of destinations; we also use it to DKIM/SPF
authenticate any outbound messages.

The domain is home to several
[contact](https://twistedmatrix.com/trac/wiki/Security)
[aliases](https://github.com/twisted/twisted/blob/trunk/code_of_conduct.md), a
few
[mailing](https://twistedmatrix.com/cgi-bin/mailman/listinfo/twisted-python/)
[lists](https://twistedmatrix.com/cgi-bin/mailman/listinfo/twisted-python/),
and of course project members get their own twistedmatrix.com forwarding
emails.  Finally, since Mailgun makes heavy usage of Twisted as part of their
own system, this allows us to use a nice software-as-a-service package while at
the same time dogfooding at least a little bit.

However, this is a bit of an oddball use-case for mailgun; [the fine folks at
Mailgun themselves will tell
you](https://documentation.mailgun.com/en/latest/faqs.html#can-i-use-mailgun-for-my-personal-email-address)
that hosting your personal email there is probably a bad idea; it's really
designed for transactional email for applications.  Nevertheless, we find it
*mostly* strikes a nice balance for our use-cases.

Mailgun is definitely optimized for bulk, API-based message sending though, and
one way this interferes with our use case for it as a mail forwarder for our
users and smarthost for our mailing lists is that bounces are never reported to
their sender; since they expect that you're using the API, you would deal with
a bounce via the API.  Furthermore, one "permanent failure" to a particular
address creates a "suppression", which means you can never, ever send mail
there again.

This has three problematic interactions with our oddball use-case:

1. Users don't find out that their messages weren't delivered, since they're
   sending them via SMTP.
2. Our mailing list software (Mailman) needs to see delivery status information
   so it can automatically unsubscribe users; if it doesn't have access to
   bounces, it thinks they're subscribed forever.
3. Mail servers configured to use black-hole lists sometimes report bogus
   "permanent" failures even when the failure is actually temporary.  We don't
   want this to prevent delivery forever, for all our users.  (Mail server
   administrators: please, please don't use IP-based blackhole lists.  They
   aren't good at blocking spam; spammers can get new IPs easily.  We have DKIM
   keys.  Verify them.  Trust our *domain* reputation, not our IP addresses.)

This script converts from the Mailgun 'bounce' API's entities into RFC6522
Delivery Status Notification messages and delivers them back to the sender,
just as a "regular" UNIX-y mail server might do.  This lets authenticated users
know if they send a message that doesn't get delivered, and it lets any of our
automated systems know about bounces they might be generating.  It also cleans
up the suppressions list so that if those addreses become valid again in the
future, we don't find ourselves mysteriously prohibited from sending mail to
them.

If you're using Mailgun as a mail forwarder, or as a mixed routing system for a
domain that handles both transactional and personal (or human-generated, like
support) email, you might find this little pile of hacks useful.
