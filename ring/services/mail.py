"""Email notifications for the NLNOG RING domain.

Faithful port of the _MAIL templates and generate_* methods in ring-admin.py.
"""

import smtplib
from string import Template

from django.conf import settings
from unidecode import unidecode

from ring.services.geocoding import countryname

ADMINEMAIL = settings.RING_ADMINEMAIL
RINGUSERSEMAIL = settings.RING_RINGUSERSEMAIL

WELCOMEMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: Welcome to the RING, $company

Dear $company

The machine you provided is part of the ring now!

The following information is vital:

    - Your company's username is "$username"
    - The user "$username" has sudo rights on $hostname (please don't break ansible :-))

If you want to add additional SSH keys, update the 'ssh-keys' file located on
'manage.ring.nlnog.net' in your home directory. Please contact a RING Admin if
you cannot login to manage.ring.nlnog.net. If you update the 'ssh-keys' file
the changes are usually propagated within 30 to 60 minutes.

For further instruction on how to use the ring, see: https://ring.nlnog.net/user-guide/

You are welcome to join the ring-users mailinglist, which is used for
announcements and discussions among RING users. You can find the list at
http://mailman.nlnog.net/mailman/listinfo/ring-users

We also operate an IRC channel: #ring on IRCnet and we have a #ring channel on the 
NLNOG Discord server: https://nlnog.net/discord.

Finally, you are invited to peer with our looking glass. The looking glass
service is available for everyone via https://lg.ring.nlnog.net/. Peering with this 
Looking Glass is optional for ring users. If you want to peer with NLNOG RING, configure a 
session with the following information:

    AS: 199036
    IPv4: 212.114.120.72
    IPv6: 2001:7b8:62b:1:0:d4ff:fe72:7848
    Type: eBGP Multi-Hop
    Policy: import NONE from AS199036, export ANY

To configure the NLNOG RING side, submit a pull request for:

    https://github.com/NLNOG/ring-ansible/blob/master/roles/openbgpd/vars/peers.yml

Kind regards,

NLNOG RING Admins

ps. An example ~/.ssh/config for usage on your workstation:

Host *.ring.nlnog.net
    User $username
    IdentityFile /Users/username/.ssh/id_rsa_nlnogring
    IdentitiesOnly yes
"""

ANNOUNCEMAIL = """From: $adminemail
To: $ringusers
Subject: $company ($countrycode) joined the RING

Dear All,

$company - AS $autnum - joined the RING today.

    "$companydesc"

Users can connect to $hostname, which is located in $country.

Your ssh keys have been distributed to the new ring node, and the ssh host
keys of $hostname have been added to /etc/ssh/ssh_known_hosts on all RING
nodes.

Kind regards,

NLNOG RING Admins
"""

DOWNMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: Problem with NLNOG RING node for $company

Dear $company

It appears the node you made available to the NLNOG RING project is no longer
online. Can you please investigate and let us know whether you could remedy
this situation?

This concerns the machine with hostname "$hostname"

For details on the problem, please see: http://$hostname/status.json or run
'ring-health -r' on the command line.

Kind regards,

NLNOG RING Admins
"""

V4DOWNMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: Problem with IPv4 connectivity on NLNOG RING node for $company

Dear $company

It appears the node you made available to the NLNOG RING project is no longer
online on IPv4. Can you please investigate and let us know whether you could
remedy this situation?

This concerns the machine with hostname "$hostname"

For details on the problem, please see: http://$hostname/status.json or run
'ring-health -r' on the command line.

Kind regards,

NLNOG RING Admins
"""

V6DOWNMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: Problem with IPv6 connectivity on NLNOG RING node for $company

Dear $company

It appears the node you made available to the NLNOG RING project is no longer
online on IPv6. Can you please investigate and let us know whether you could
remedy this situation?

This concerns the machine with hostname "$hostname"

For details on the problem, please see: http://$hostname/status.json or run
'ring-health -r' on the command line.

Kind regards,

NLNOG RING Admins
"""

REMOVEMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: NLNOG RING node for $company removed

Dear $company

Please be informed that the node with hostname "$hostname" is no longer
part of the NLNOG RING. The machine can be shut down or used for other purposes.

All references to the machine have been removed from our database. If you
have no additional nodes that are part of the RING your user account(s) will
be deactivated.

This email notification is part of an automated cleanup process to remove
RING nodes which have been unreachable for an extended period of time.

If you'd like a machine to rejoin the NLNOG RING please reply to this message
or fill in the form at https://ring.nlnog.net/contact/application-form/

Kind regards,

NLNOG RING Admins
"""

ANSIBLEREPORTEMAIL = """From: $adminemail
To: $adminemail
Subject: NLNOG RING Ansible report

$contents

Please investigate new missing nodes and failed ansible runs.
Use "ring-admin send downmail <node>" to request assistance from the node owner.

Kind regards,

NLNOG RING dbmaster
"""

HUGOPOST = """+++
author = "RING Admins"
title = "$company ($countrycode) joined the RING"
date = "$date"
description = "$company ($countrycode) joined the RING"
categories = [
    "participants",
]
+++

$company - AS $autnum - joined the RING today.

> $companydesc

Users can connect to $hostname, which is located in $country.
"""

FAILEDUPGRADEMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: Failed Upgrade of NLNOG RING node for $company

Dear $company

It appears the node you made available to the NLNOG RING project is no longer
online after the scheduled upgrade to Ubuntu 22.04.  Can you please investigate.
If you're able to fix the node, please do so and let us know.

If not, please provision a fresh install with the following details:
 * Ubuntu 22.04
 * user 'nlnog' with the SSH-keys mentioned on https://ring.nlnog.net/contact/
 * make sure this user has passwordless 'sudo' rights

This concerns the machine with hostname "$hostname"

Kind regards,

NLNOG RING Admins
"""

CANNOTUPGRADEMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: Failed Upgrade of NLNOG RING node $hostname for $company

Dear $company

It appears the node you made available to the NLNOG RING project failed to
upgrade to Ubuntu 22.04. Can you please try to fix the upgrade, or if that fails
deploy a fresh install with the following details:

 * Ubuntu 22.04
 * user 'nlnog' with the SSH-keys mentioned on https://ring.nlnog.net/contact/
 * make sure this user has passwordless 'sudo' rights

This concerns the machine with hostname "$hostname"

Kind regards,

NLNOG RING Admins
"""

DISKMAIL = """From: $adminemail
To: $nocemail, $email
Cc: $adminemail
Subject: Problem with NLNOG RING node for $company

Dear $company

The node you made available to the NLNOG RING project has run out of available
disk space. Could you please remedy this?

Please make the root partition at least 20GB, and if your machine has a 
dedicated /boot partition, please make sure it is at least 1GB so new kernels
can be installed.

This concerns the machine with hostname "$hostname"

Kind regards,

NLNOG RING Admins
"""


class RingMailError(Exception):
    pass


def _participant_for_hostname(hostname):
    try:
        machine = Machine.objects.get(hostname=hostname)
    except Machine.DoesNotExist:
        raise RingMailError("machine %s not found" % hostname)
    return machine.owner.participant


def _participant_for_username(username):
    try:
        user = RingUser.objects.get(username=username)
    except RingUser.DoesNotExist:
        raise RingMailError("user %s not found" % username)
    return user.participant


def sendmail(sender, recipients, message):
    server = smtplib.SMTP(settings.RING_MAILHOST)
    message = unidecode(message)
    server.sendmail(sender, recipients, message)
    server.quit()


def generate_welcomemail(username, send=None):
    p = _participant_for_username(username)
    u = RingUser.objects.get(participant=p)
    m = u.machines.all()
    hostname = ""
    mfirst = m.first()
    if mfirst:
        hostname = mfirst.hostname
    text = Template(WELCOMEMAIL)
    mailbody = text.substitute(
        company=p.company,
        nocemail=p.nocemail,
        email=p.email,
        hostname=hostname,
        username=u.username,
        adminemail=ADMINEMAIL,
    )
    print(mailbody)

    if send:
        print("Sending mail... ")
        sendmail(ADMINEMAIL, [p.nocemail, p.email, ADMINEMAIL], mailbody)
        print("Done.")


def generate_announcemail(username, send=None):
    p = _participant_for_username(username)
    u = RingUser.objects.get(participant=p)
    mfirst = u.machines.all().first()
    if not mfirst:
        raise RingMailError("no machines found for %s" % username)
    hostname = mfirst.hostname
    autnum = "AS%s" % mfirst.autnum
    country = countryname(mfirst.country, None)
    text = Template(ANNOUNCEMAIL)
    mailbody = text.substitute(
        company=p.company,
        companydesc=p.companydesc,
        hostname=hostname,
        autnum=autnum,
        dc=mfirst.dc or "",
        state=mfirst.state or "",
        country=country,
        countrycode=mfirst.country,
        adminemail=ADMINEMAIL,
        ringusers=RINGUSERSEMAIL,
    )
    print(mailbody)

    if send:
        print("Sending mail... ")
        sendmail(ADMINEMAIL, RINGUSERSEMAIL, mailbody)
        print("Done.")


def generate_downmail(hostname, send=None, template=DOWNMAIL):
    p = _participant_for_hostname(hostname)
    text = Template(template)
    mailbody = text.substitute(
        company=p.company,
        nocemail=p.nocemail,
        email=p.email,
        hostname=hostname,
        adminemail=ADMINEMAIL,
    )
    print(mailbody)

    if send:
        print("Sending mail... ")
        sendmail(ADMINEMAIL, [p.nocemail, p.email, ADMINEMAIL], mailbody)
        print("Done.")


def generate_removemail(hostname, send=None):
    generate_downmail_wrapper(hostname, send, REMOVEMAIL)


def generate_failedupgrademail(hostname, send=None, template=FAILEDUPGRADEMAIL):
    generate_downmail_wrapper(hostname, send, template)


def generate_cannotupgrademail(hostname, send=None, template=CANNOTUPGRADEMAIL):
    generate_downmail_wrapper(hostname, send, template)


def generate_diskmail(hostname, send=None, template=DISKMAIL):
    generate_downmail_wrapper(hostname, send, template)


def generate_downmail_wrapper(hostname, send, template):
    p = _participant_for_hostname(hostname)
    text = Template(template)
    mailbody = text.substitute(
        company=p.company,
        nocemail=p.nocemail,
        email=p.email,
        hostname=hostname,
        adminemail=ADMINEMAIL,
    )
    print(mailbody)

    if send:
        print("Sending mail... ")
        sendmail(ADMINEMAIL, [p.nocemail, p.email, ADMINEMAIL], mailbody)
        print("Done.")


def generate_downreminders(send=None):
    reminders = []
    for m in Machine.objects.filter(active=0):
        owner = m.owner
        if owner.active == 0:
            continue
        if owner.admin == 1:
            continue
        reminders.append(m.hostname)

    v4_reminders = []
    v6_reminders = []
    for m in Machine.objects.filter(active=1):
        owner = m.owner
        if owner.active == 0:
            continue
        if owner.admin == 1:
            continue
        if m.alive_v4 == 0 and m.alive_v6 == 0:
            reminders.append(m.hostname)
        elif m.v4 and m.alive_v4 == 0:
            v4_reminders.append(m.hostname)
        elif m.v6 and m.alive_v6 == 0:
            v6_reminders.append(m.hostname)

    print("Inactive machines with active owners:")
    for host in sorted(reminders):
        print("- %s" % (host))
    print("")
    print("Total: %d" % (len(reminders)))
    print("Active machines with broken IPv4:")
    for host in sorted(v4_reminders):
        print("- %s" % (host))
    print("")
    print("Total: %d" % (len(v4_reminders)))
    print("Active machines with broken IPv6:")
    for host in sorted(v6_reminders):
        print("- %s" % (host))
    print("")
    print("Total: %d" % (len(v6_reminders)))

    if send:
        print("Sending mail")
        for host in sorted(reminders):
            print("...%s" % (host))
            generate_downmail(host, send=1)
            time.sleep(1)
        for host in sorted(v4_reminders):
            print("...%s" % (host))
            generate_downmail(host, send=1, template=V4DOWNMAIL)
            time.sleep(1)
        for host in sorted(v6_reminders):
            print("...%s" % (host))
            generate_downmail(host, send=1, template=V6DOWNMAIL)
            time.sleep(1)
        print("Done.")


def send_ansible_report(report):
    text = Template(ANSIBLEREPORTEMAIL)
    mailbody = text.substitute(adminemail=ADMINEMAIL, contents=report)
    print(mailbody)
    sendmail(ADMINEMAIL, [ADMINEMAIL], mailbody)