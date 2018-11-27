#!/usr/bin/env python3.6

"""
Script to generate rfc6522 DSN messages from mailgun.
"""

import sys
import os

from io import BytesIO

from textwrap import dedent
from secretly import secretly

import argparse
import datetime
import dateutil.parser
import itertools
import json
import pytz

from email.utils import make_msgid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import message_from_string
from email.message import Message
from email.mime.nonmultipart import MIMEBase
from email.utils import formatdate

import treq
from twisted.internet.defer import inlineCallbacks, succeed
from random import SystemRandom
choice = SystemRandom().choice

class UnexpectedResponse(Exception):
    """
    The response wasn't expected.
    """

@inlineCallbacks
def getAllEvents(domain, secret):
    eventsURL = "https://api.mailgun.net/v3/{domain}/events".format(
        domain=domain
    )
    pageURL = eventsURL
    while True:
        thisPage = (yield treq.json_content(
            (yield treq.get(pageURL, auth=("api", secret),
                            params={
                                "event": "failed",
                                "severity": "permanent",
                            }))
        ))
        if not thisPage['items']:
            break
        for item in thisPage['items']:
            if ((item['flags'].get('is-authenticated') or
                 item['flags'].get('is-delayed-bounce')
                 # delayed bounces are (obviously) not authenticated.  has
                 # mailgun already validated this bounce?  if not, we might
                 # need to check to make sure the message ID originated from a
                 # delayed bounce.
            ) and
                item['reason'] in ('bounce', 'suppress-bounce')):
                bounced_at = pytz.utc.localize(
                    datetime.datetime.utcfromtimestamp(item['timestamp'])
                )
                print("found a bounce for", item['recipient'], 'on',
                      bounced_at)
                bounce_uri = (
                    "https://api.mailgun.net/v3/{domain}/bounces/{recipient}"
                    .format(domain=domain,
                            recipient=item['recipient'].lower())
                )
                bounced = yield treq.get(bounce_uri, auth=("api", secret))
                if bounced.code == 200:
                    bouncedata = yield treq.json_content(bounced)
                    suppression_created = dateutil.parser.parse(
                        bouncedata.get('created_at')
                    )
                    if item['flags'].get('is-delayed-bounce'):
                        original = (yield treq.json_content(
                            (yield treq.get(
                                eventsURL,
                                auth=("api", secret),
                                params={
                                    "message-id": item['message']['headers']['message-id'],
                                }))
                        ))['items'][-1]
                        item['storage'] = original['storage']
                        item['envelope'] = original['envelope']
                    yield writeone(secret, item, domain)
                    delta = abs(
                        (bounced_at - suppression_created).total_seconds()
                    )
                    if item['reason'] == 'bounce':
                        print("    clearing (original) bounce created",
                              suppression_created, "with delta", delta)
                        yield treq.delete(bounce_uri, auth=("api", secret))
                    else:
                        print("    not clearing (suppression) bounce; delta:",
                              delta, 'reason:', item['reason'])
                else:
                    # drain the response
                    treq.content(bounced)
                    print("    no suppression; not sending bounce.")
        pageURL = thisPage['paging']['next']


@inlineCallbacks
def writeone(secret, blob, domain, counter=itertools.count()):
    msg = MIMEMultipart("report", **{"report-type": "delivery-status"})
    to_address = blob['envelope']['sender']
    msg['to'] = to_address
    msg['from'] = '"Bounce Generator" <bounce-generator@{domain}>'.format(
        domain=domain
    )
    msg['subject'] = 'Delivery Status Notification: Deferred Bounce'
    msg['message-id'] = make_msgid("mg2dsn", domain=domain)
    originalMessageID = blob['message']['headers']['message-id']
    msg['in-reply-to'] = originalMessageID
    msg['references'] = originalMessageID
    jsonified = json.dumps(blob, indent=2)
    statthing = MIMEBase("message", "delivery-status")

    storurl = blob['storage']['url']
    apiresponse = yield treq.get(storurl, auth=("api", secret),
                                 headers={"accept": ["message/rfc2822"]})
    msgobj = yield treq.json_content(apiresponse)
    if apiresponse.code == 200:
        original_bytes = msgobj['body-mime']
    else:
        original_bytes = dedent(
            """
            From: no-user@no-host.invalid
            Subject: original message deleted
            Content-Type: text/plain

            Sorry, the mail administrator didn't run the script soon enough
            and the original message got garbage-collected in the meanwhile.
            """
        ).strip()

    original = message_from_string(original_bytes)

    inner1 = Message()

    inner1['Reporting-MTA'] = 'dns;{domain}'.format(domain=domain)
    inner1['Arrival-Date'] = formatdate(localtime=True)
    inner1['Original-Envelope-Id'] = originalMessageID

    inner2 = Message()

    inner2['Original-Recipient'] = 'rfc822;' + blob['envelope']['targets']
    inner2['Final-Recipient'] = 'rfc822;' + blob['envelope']['targets']
    inner2['Action'] = 'failed'
    inner2['Status'] = '5.1.0 (Remote SMTP server has rejected address)'
    inner2['Remote-MTA'] = 'dns;' + blob['delivery-status'].get(
        'mx-host', 'no-host.invalid'
    )
    inner2['Diagnostic-Code'] = 'smtp;' + str(blob['delivery-status']['code'])

    statthing.attach(inner1)
    statthing.attach(inner2)

    msg.attach(MIMEText(
        dedent("""
               This is the mail system at {domain}.  We are sorry to
               inform you that the following message, which we believe you
               sent, could not be delivered.

               ---
               From: {sender}
               To: {recipient}
               Subject: {subject}
               ---
               """.format(
                   domain=domain,
                   sender=to_address,
                   recipient=blob['envelope']['targets'],
                   subject=blob['message']['headers'].get('subject',
                                                          '(no subject)'),
               )),
        "plain", "utf-8"
    ))
    msg.attach(statthing)
    msg.attach(original)
    msg.attach(
        MIMEText(
            "\n---\n\noriginal mailgun delivery status failure follows:\n\n{}\n\n"
            .format(jsonified), "plain", "utf-8"
        ),
    )

    response = yield treq.post(
        "https://api.mailgun.net/v3/{domain}/messages.mime".format(
            domain=domain
        ),
        auth=("api", secret),
        params={
            "to": to_address,
        },
        files={
            "message": ("message", BytesIO(msg.as_string().encode("utf-8"))),
        }
    )
    print("bounce sent:", response.code)
    print((yield treq.json_content(response)))

def main(reactor, argv):
    from twisted.python.filepath import FilePath

    cfg = FilePath(os.path.expanduser("~/.config/mg2dsn"))
    cfg.makedirs(True)
    defaultsPath = cfg.child("defaults.json")
    if defaultsPath.exists():
        defaults = json.loads(defaultsPath.getContent().decode('utf-8'))
    else:
        defaults = {}
    unspecified = object()
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", nargs="?",
                        default=defaults.get("domain", unspecified))
    namespace = parser.parse_args(argv[1:])
    if namespace.domain is unspecified:
        print("Please specify a domain.")
        return succeed(1)
    defaultsPath.setContent(json.dumps(dict(defaults, domain=namespace.domain),
                                       indent=2).encode())
    def action(secret):
        return getAllEvents(namespace.domain, secret)
    return secretly(reactor, action=action,
                    system='api.mailgun.net',
                    username=namespace.domain)

def script():
    from twisted.internet.task import react
    react(main, [sys.argv])
