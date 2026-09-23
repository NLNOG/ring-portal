"""git/rsync orchestration for the ring-ansible and ring-web repositories.

Ports of ring-admin.py's ansible_* and web_* helpers. These touch real paths
under RING_ANSIBLEDIR / RING_WEBDIR and are only invoked via management
commands.
"""

import getpass
import os
import sys
from pathlib import Path

from django.conf import settings

from ring.services import ansiblefiles


def _run(cmd):
    ret = os.system(cmd)
    if ret > 0:
        sys.exit("subprocess returned error: %d" % (ret))


def ansible_checkout():
    _run("cd %s && git checkout" % settings.RING_ANSIBLEDIR)
    _run("cd %s && git pull" % settings.RING_ANSIBLEDIR)


def ansible_getsshkeys():
    _run(
        "rsync -r --delete --chmod=g+w %s %s/%s"
        % (settings.RING_ANSIBLE_KEYORIGIN, settings.RING_ANSIBLEDIR, settings.RING_ANSIBLE_KEYBASE)
    )
    _run(
        "cd %s && git add %s/%s"
        % (settings.RING_ANSIBLEDIR, settings.RING_ANSIBLEDIR, settings.RING_ANSIBLE_KEYDIR)
    )


def ansible_write_hostfile():
    d = Path(settings.RING_ANSIBLEDIR)
    for fname in (settings.RING_ANSIBLE_HOSTFILE, settings.RING_ANSIBLE_HOSTFILE + ".active"):
        (d / fname).write_text(ansiblefiles.hostfile_text())


def ansible_write_hostkeyfile():
    d = Path(settings.RING_ANSIBLEDIR)
    (d / settings.RING_ANSIBLE_HOSTKEYFILE).write_text(ansiblefiles.hostkeyfile_text())


def ansible_write_userfile():
    d = Path(settings.RING_ANSIBLEDIR)
    (d / settings.RING_ANSIBLE_USERFILE).write_text(ansiblefiles.userfile_text())


def ansible_push():
    username = getpass.getuser()
    _run(
        "cd %s && git commit -am 'dbmaster commit by %s'" % (settings.RING_ANSIBLEDIR, username)
    )
    _run("cd %s && git push" % settings.RING_ANSIBLEDIR)


def ansible_deploy():
    ansible_checkout()
    ansible_getsshkeys()
    ansible_write_hostfile()
    ansible_write_hostkeyfile()
    ansible_write_userfile()
    ansible_push()


def web_checkout():
    _run("cd %s && git checkout" % settings.RING_WEBDIR)
    _run("cd %s && git pull" % settings.RING_WEBDIR)


def web_push():
    username = getpass.getuser()
    _run("cd %s && git commit -am 'dbmaster commit by %s'" % (settings.RING_WEBDIR, username))
    _run("cd %s && git push" % settings.RING_WEBDIR)


def web_publish(username, postbody):
    web_checkout()
    postdir = Path(settings.RING_WEB_POSTDIR)
    logodir = Path(settings.RING_WEB_LOGODIR)
    postdir.mkdir(parents=True, exist_ok=True)
    post_filename = postdir / ("%s-joined-the-ring.md" % username)
    logo_filename = logodir / ("%s.png" % username)
    post_shortloc = post_filename.as_posix().replace(settings.RING_WEBDIR + "/", "")
    if not logo_filename.is_file():
        sys.stderr.write(
            "logo for %s missing; please add it to %s in the ring-web repository.\n"
            % (username, logo_filename.as_posix().replace(settings.RING_WEBDIR + "/", ""))
        )
    post_filename.write_text(postbody)
    _run("cd %s && git add %s" % (settings.RING_WEBDIR, post_shortloc))
    web_push()