from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from ring.models import (
    AnsibleRun,
    HealthReport,
    Machine,
    MachineRemark,
    Participant,
    ParticipantRemark,
    PeeringDBSignup,
    RingSignup,
    RingUser,
    RingUserProfile,
    SSHKey,
)
from ring.services.profiles import (
    link_ring_user,
    participant_autnum,
    participant_for_autnum,
    ring_user_for_pdb,
    set_participant_autnum,
)


def make_owner(username="owner1"):
    p = Participant.objects.create(company=username + "-co")
    return RingUser.objects.create(username=username, participant=p, userid=5000, active=1)


PDB_PROFILE = {
    "id": 9001,
    "name": "Ring Peering",
    "given_name": "Ring",
    "family_name": "Peering",
    "email": "peering@example.net",
    "verified_email": True,
    "networks": [
        {"perms": 15, "asn": 2914, "name": "Example Net", "id": 99}
    ],
}
PDB_SETTINGS = {
    "PDB_CLIENT_ID": "cid",
    "PDB_CLIENT_SECRET": "secret",
    "PDB_REDIRECT_URL": "https://rh.example/cb/",
}


class ApiCrudTest(TestCase):
    def setUp(self):
        self.owner = make_owner()
        self.machine = Machine.objects.create(
            hostname="node01.ring.nlnog.net", owner=self.owner, autnum=123, active=1
        )
        self.user = User.objects.create_user(username="apiuser", password="pw")
        self.user.is_staff = True
        self.user.save()
        self.admin = APIClient()
        self.admin.force_authenticate(user=self.user)
        self.anon = APIClient()

    def test_participant_list_public(self):
        r = self.anon.get("/api/participants/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)

    def test_machine_search(self):
        r = self.anon.get("/api/machines/?search=node01")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)

    def test_machine_detail(self):
        r = self.anon.get("/api/machines/%d/" % self.machine.pk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["hostname"], "node01.ring.nlnog.net")
        self.assertEqual(r.data["short_hostname"], "node01")

    def test_machine_create_requires_auth(self):
        r = self.anon.post(
            "/api/machines/",
            {"hostname": "x.ring.nlnog.net", "owner": self.owner.pk, "autnum": 1},
            format="json",
        )
        self.assertIn(r.status_code, (401, 403))

    def test_machine_create_authorized(self):
        r = self.admin.post(
            "/api/machines/",
            {"hostname": "node02.ring.nlnog.net", "owner": self.owner.pk, "autnum": 1},
            format="json",
        )
        self.assertEqual(r.status_code, 201)

    def test_health_ingest_list_post_only(self):
        r = self.anon.get("/api/health/")
        self.assertEqual(r.status_code, 405)

    def test_health_ingest_requires_auth(self):
        r = self.anon.post(
            "/api/health/",
            {"hostname": "node01", "family": 4, "summary": {}},
            format="json",
        )
        self.assertIn(r.status_code, (401, 403))

    def test_health_ingest_authorized(self):
        r = self.admin.post(
            "/api/health/",
            {"hostname": "node01", "family": 4, "summary": {"info": {"success": True}}},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(HealthReport.objects.count(), 1)

    def test_health_ingest_batch(self):
        r = self.admin.post(
            "/api/health/",
            [
                {"hostname": "node01", "family": 4, "summary": {}},
                {"hostname": "node02", "family": 6, "summary": {}},
            ],
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(HealthReport.objects.count(), 2)

    def test_health_ingest_invalid_summary(self):
        r = self.admin.post(
            "/api/health/",
            {"hostname": "node01", "family": 999, "summary": 123},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_ansible_ingest_authorized(self):
        r = self.admin.post(
            "/api/ansible/",
            {"hostname": "node01", "unreachable": 0, "ok": 5, "failures": 0},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(AnsibleRun.objects.count(), 1)

    def test_health_ingest_update_fields(self):
        r = self.admin.post(
            "/api/health/",
            {
                "hostname": "node01",
                "family": 4,
                "summary": {
                    "info": {
                        "needs_reboot": True,
                        "ubuntu_updates": 12,
                        "ubuntu_security_updates": 3,
                    },
                    "health": {},
                },
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        hp = HealthReport.objects.get()
        self.assertIs(hp.needs_reboot, True)
        self.assertEqual(hp.ubuntu_updates, 12)
        self.assertEqual(hp.ubuntu_security_updates, 3)


def make_org_user(username, company, password="pw", admin=False, link=True):
    participant = Participant.objects.create(company=company)
    user = User.objects.create_user(username=username, password=password)
    ring_user = RingUser.objects.create(
        username=username, participant=participant, active=1, admin=admin
    )
    if link:
        link_ring_user(user, ring_user)
    return user, participant, ring_user


class OrgScopedApiTest(TestCase):
    """Ordinary users may only write their own participant's objects."""

    def setUp(self):
        self.our_user, self.our_org, self.our_ru = make_org_user(
            "alice", "Alice Corp"
        )
        self.other_user, self.other_org, self.other_ru = make_org_user(
            "bob", "Bob Inc"
        )
        self.our_machine = Machine.objects.create(
            hostname="alice01.ring.nlnog.net", owner=self.our_ru, autnum=1, active=1
        )
        self.other_machine = Machine.objects.create(
            hostname="bob01.ring.nlnog.net", owner=self.other_ru, autnum=2, active=1
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.our_user)

    def test_org_user_create_own_machine(self):
        r = self.client.post(
            "/api/machines/",
            {
                "hostname": "alice02.ring.nlnog.net",
                "owner": self.our_ru.pk,
                "autnum": 3,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Machine.objects.filter(hostname="alice02.ring.nlnog.net").count(), 1)

    def test_org_user_create_machine_of_other_org(self):
        r = self.client.post(
            "/api/machines/",
            {
                "hostname": "evil.ring.nlnog.net",
                "owner": self.other_ru.pk,
                "autnum": 3,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("owner", r.data)

    def test_org_user_update_own_and_other_machine(self):
        ok = self.client.patch(
            "/api/machines/%d/" % self.our_machine.pk, {"city": "Utrecht"}, format="json"
        )
        self.assertEqual(ok.status_code, 200)
        nok = self.client.patch(
            "/api/machines/%d/" % self.other_machine.pk, {"city": "Geneva"}, format="json"
        )
        self.assertIn(nok.status_code, (403, 404))

    def test_org_user_delete_only_own_machine(self):
        nok = self.client.delete("/api/machines/%d/" % self.other_machine.pk)
        self.assertIn(nok.status_code, (403, 404))
        ok = self.client.delete("/api/machines/%d/" % self.our_machine.pk)
        self.assertEqual(ok.status_code, 204)

    def test_org_user_reads_everything(self):
        r = self.client.get("/api/machines/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 2)

    def test_org_user_cannot_grant_admin_flag(self):
        r = self.client.post(
            "/api/users/",
            {
                "username": "mallory",
                "participant": self.our_org.pk,
                "admin": True,
                "active": True,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("admin", r.data)

    def test_org_user_cannot_create_participant(self):
        r = self.client.post(
            "/api/participants/",
            {"company": "Brand New Org", "public": True},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_org_user_ingest_own_machine_only(self):
        ok = self.client.post(
            "/api/health/",
            {"hostname": "alice01", "family": 4, "summary": {}},
            format="json",
        )
        self.assertEqual(ok.status_code, 201)
        nok = self.client.post(
            "/api/health/",
            {"hostname": "bob01", "family": 4, "summary": {}},
            format="json",
        )
        self.assertEqual(nok.status_code, 403)
        self.assertEqual(HealthReport.objects.count(), 1)

    def test_org_user_ingest_unknown_host_denied(self):
        r = self.client.post(
            "/api/ansible/",
            {"hostname": "ghost", "unreachable": 0, "ok": 1, "failures": 0},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_org_user_remark_machine_scoped(self):
        r = self.client.post(
            "/api/mremarks/",
            {"remark": "bob's box", "machine": self.other_machine.pk},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        r2 = self.client.post(
            "/api/mremarks/",
            {"remark": "our box", "machine": self.our_machine.pk},
            format="json",
        )
        self.assertEqual(r2.status_code, 201)

    def test_org_user_rejects_fk_inside_other_org(self):
        other_ssh = SSHKey.objects.create(
            keytype="ssh-ed25519", keyid="k", user=self.other_ru, sshkey="AAA"
        )
        r = self.client.patch(
            "/api/sshkeys/%d/" % other_ssh.pk, {"keyid": "renamed"}, format="json"
        )
        self.assertIn(r.status_code, (403, 404))
        r2 = self.client.post(
            "/api/sshkeys/",
            {"keytype": "ssh-ed25519", "keyid": "k2", "sshkey": "BBB",
             "user": self.other_ru.pk},
            format="json",
        )
        self.assertEqual(r2.status_code, 400)


class AdminBroadApiTest(TestCase):
    """Ring admins bypass org scoping entirely."""

    def setUp(self):
        self.u1, self.o1, self.ru1 = make_org_user("eve", "Eve Org", admin=True)
        self.u2, self.o2, self.ru2 = make_org_user("frank", "Frank Org")
        self.m1 = Machine.objects.create(
            hostname="eve01.ring.nlnog.net", owner=self.ru1, autnum=1, active=1
        )
        self.m2 = Machine.objects.create(
            hostname="frank01.ring.nlnog.net", owner=self.ru2, autnum=2, active=1
        )
        self.admin = APIClient()
        self.admin.force_authenticate(user=self.u1)
        self.p1 = None

    def test_ring_admin_writes_any_org(self):
        r = self.admin.patch(
            "/api/machines/%d/" % self.m2.pk, {"city": "Bonn"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.m2.refresh_from_db()
        self.assertEqual(self.m2.city, "Bonn")

    def test_ring_admin_ingest_any_host(self):
        r = self.admin.post(
            "/api/health/",
            {"hostname": "frank01", "family": 4, "summary": {}},
            format="json",
        )
        self.assertEqual(r.status_code, 201)


class AccountApiTest(TestCase):
    def setUp(self):
        self.user, self.org, self.ru = make_org_user("clara", "Clara Org")
        self.client = APIClient()

    def test_login_and_me(self):
        r = self.client.post(
            "/api/account/login/",
            {"username": "clara", "password": "pw"},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        payload = r.data
        self.assertEqual(payload["username"], "clara")
        self.assertEqual(payload["participant"], self.org.pk)
        self.assertTrue(payload["token"])
        token = payload["token"]

        me = APIClient()
        me.credentials(HTTP_AUTHORIZATION="Token " + token)
        r2 = me.get("/api/account/me/")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["ring_username"], "clara")
        self.assertIs(r2.data["is_ring_admin"], False)

    def test_login_bad_password(self):
        r = self.client.post(
            "/api/account/login/",
            {"username": "clara", "password": "wrong"},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_me_requires_auth(self):
        r = self.client.get("/api/account/me/")
        self.assertEqual(r.status_code, 403)

    def test_ring_admin_flag_reported(self):
        u2, _, ru2 = make_org_user("dave", "Dave Org", admin=True)
        self.client.force_authenticate(user=u2)
        r = self.client.get("/api/account/me/")
        self.assertTrue(r.data["is_ring_admin"])


class SignupFlowTest(TestCase):
    def setUp(self):
        self.org = Participant.objects.create(company="Cycle Org")
        self.boss = User.objects.create_superuser("boss", "boss@x", "pw")
        boss_ring_user = RingUser.objects.create(
            username="boss", participant=self.org, active=1, admin=1,
        )
        link_ring_user(self.boss, boss_ring_user)
        self.anon = Client()
        self.managed = Client()
        self.managed.login(username="boss", password="pw")

    def test_signup_approve_cycle(self):
        r = self.anon.post(
            "/accounts/signup/",
            {
                "username": "newbie",
                "email": "newbie@x",
                "password": "pw1",
                "password2": "pw1",
                "company": str(self.org.pk),
            },
        )
        self.assertEqual(r.status_code, 302)
        signup = RingSignup.objects.get(django_user__username="newbie")
        self.assertIs(signup.approved, False)
        self.assertIs(signup.django_user.is_active, False)

        r2 = self.managed.post(
            "/accounts/signups/%d/approve/" % signup.pk, {"role": "user"}
        )
        self.assertEqual(r2.status_code, 302)
        signup.refresh_from_db()
        signup.django_user.refresh_from_db()
        self.assertIs(signup.approved, True)
        self.assertIs(signup.django_user.is_active, True)
        self.assertIsNotNone(Token.objects.filter(user=signup.django_user).first())

        anon_view = self.anon.get("/accounts/signups/")
        self.assertEqual(anon_view.status_code, 302)  # redirect to login

    def test_signup_approve_blocked_when_legacy_readonly(self):
        self.anon.post(
            "/accounts/signup/",
            {
                "username": "newbie2",
                "email": "newbie2@x",
                "password": "pw1",
                "password2": "pw1",
                "company": str(self.org.pk),
            },
        )
        signup = RingSignup.objects.get(django_user__username="newbie2")
        with mock.patch("ring.views.legacy_writable", return_value=False):
            r = self.managed.post(
                "/accounts/signups/%d/approve/" % signup.pk, {"role": "user"}
            )
        self.assertEqual(r.status_code, 302)
        signup.refresh_from_db()
        self.assertIs(signup.approved, False)
        self.assertFalse(RingUser.objects.filter(username="newbie2").exists())

    def test_reject_removes_account(self):
        self.anon.post(
            "/accounts/signup/",
            {
                "username": "flaker",
                "email": "flaker@x",
                "password": "pw",
                "password2": "pw",
                "company": str(self.org.pk),
            },
        )
        signup = RingSignup.objects.get(django_user__username="flaker")
        self.managed.post("/accounts/signups/%d/reject/" % signup.pk)
        self.assertFalse(RingSignup.objects.filter(pk=signup.pk).exists())
        self.assertFalse(User.objects.filter(username="flaker").exists())


class ParticipantEditTest(TestCase):
    def setUp(self):
        self.usr, self.org, self.ru = make_org_user("owner_ed", "Edit Corp")
        self.other_usr, self.other_org, self.other_ru = make_org_user(
            "other_ed", "Other Corp"
        )
        self.me = Client()
        self.me.login(username="owner_ed", password="pw")
        self.other = Client()
        self.other.login(username="other_ed", password="pw")
        self.anon = Client()

    def edit_url(self):
        return "/participants/%d/edit/" % self.org.pk

    def test_own_org_can_edit(self):
        r = self.me.post(
            self.edit_url(),
            {
                "contact": "Teun",
                "email": "teun@editcorp.example",
                "nocemail": "noc@editcorp.example",
                "url": "https://editcorp.example/",
                "companydesc": "An edit corp",
                "public": "on",
                "company": "Hacked Name",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.org.refresh_from_db()
        self.assertEqual(self.org.contact, "Teun")
        self.assertEqual(self.org.nocemail, "noc@editcorp.example")
        self.assertIs(self.org.public, True)
        # non-admins cannot rename their own company
        self.assertEqual(self.org.company, "Edit Corp")

    def test_public_unchecked_clears_flag(self):
        self.org.public = True
        self.org.save()
        self.me.post(
            self.edit_url(),
            {
                "contact": self.org.contact,
                "email": self.org.email,
                "nocemail": self.org.nocemail,
            },
        )
        self.org.refresh_from_db()
        self.assertIsNone(self.org.public)

    def test_other_org_cannot_edit(self):
        r = self.other.get(self.edit_url())
        self.assertEqual(r.status_code, 302)  # redirected to login
        r2 = self.other.post(
            self.edit_url(), {"contact": "sabotage", "email": ""}
        )
        self.assertEqual(r2.status_code, 302)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.contact, "sabotage")

    def test_anon_cannot_edit(self):
        r = self.anon.get(self.edit_url())
        self.assertEqual(r.status_code, 302)

    def test_edit_list_visibility(self):
        as_owner = self.me.get("/my/")
        self.assertIn("Edit Corp", as_owner.content.decode())
        self.assertIn(self.edit_url(), as_owner.content.decode())
        as_other = self.other.get("/my/")
        # other user sees only their own org on the portal
        self.assertIn(
            "/participants/%d/edit/" % self.other_org.pk,
            as_other.content.decode(),
        )
        self.assertNotIn(self.edit_url(), as_other.content.decode())

    def test_edit_blocked_when_legacy_readonly(self):
        with mock.patch("ring.views.legacy_writable", return_value=False):
            r = self.me.post(
                self.edit_url(),
                {"contact": "Teun", "email": "teun@editcorp.example"},
            )
        self.assertEqual(r.status_code, 302)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.contact, "Teun")

    def test_admin_can_rename_company(self):
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.create_superuser(
            "big_ed", "big@x", "pw"
        )
        me = Client()
        me.login(username="big_ed", password="pw")
        r = me.post(
            self.edit_url(),
            {
                "company": "Renamed Corp",
                "contact": "",
                "email": "",
                "nocemail": "",
                "public": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.org.refresh_from_db()
        self.assertEqual(self.org.company, "Renamed Corp")
        # admin sees edit links for every participant
        page = me.get("/participants/").content.decode()
        self.assertIn(self.edit_url(), page)


class MemberPortalTest(TestCase):
    """Regular members only see their own organisation's data."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.member, self.org, self.ru = make_org_user("member1", "My Org")
        self.other, self.other_org, self.other_ru = make_org_user(
            "other1", "Secret Corp"
        )
        self.my_machine = Machine.objects.create(
            hostname="mine.ring.nlnog.net", owner=self.ru, autnum=1, active=1
        )
        self.their_machine = Machine.objects.create(
            hostname="theirs.ring.nlnog.net", owner=self.other_ru, autnum=2, active=1
        )
        self.client = Client()
        self.assertTrue(self.client.login(username="member1", password="pw"))

    def test_index_redirects_member_to_portal(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/my/")

    def test_portal_shows_own_company_account_nodes(self):
        r = self.client.get("/my/")
        self.assertEqual(r.status_code, 200)
        content = r.content.decode()
        self.assertIn("My Org", content)
        self.assertIn("mine.ring.nlnog.net", content)
        self.assertIn("member1", content)
        self.assertNotIn("Secret Corp", content)
        self.assertNotIn("theirs.ring.nlnog.net", content)

    def test_member_redirected_away_from_org_pages(self):
        for url in ("/machines/", "/participants/", "/users/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertEqual(r.url, "/my/", url)

    def test_machine_detail_own_only(self):
        own = self.client.get("/machines/%s/" % self.my_machine.hostname)
        self.assertEqual(own.status_code, 200)
        other = self.client.get("/machines/%s/" % self.their_machine.hostname)
        self.assertEqual(other.status_code, 302)
        self.assertEqual(other.url, "/my/")

    def test_portal_issues_scoped_to_own_nodes(self):
        HealthReport.objects.create(
            hostname="mine",
            family=4,
            summary={"info": {"needs_reboot": True}, "health": {}},
        )
        HealthReport.objects.create(
            hostname="theirs",
            family=4,
            summary={"info": {"needs_reboot": True}, "health": {}},
        )
        r = self.client.get("/my/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["need_reboot"], 1)
        content = r.content.decode()
        self.assertIn("mine.ring.nlnog.net", content)
        self.assertNotIn("theirs.ring.nlnog.net", content)


class HealthAlertViewsTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.owner = make_owner("alertowner")
        self.machine = Machine.objects.create(
            hostname="alert01.ring.nlnog.net", owner=self.owner, autnum=123, active=1
        )
        self.client = Client()
        viewer = User.objects.create_user(username="viewer", password="pw")
        viewer.is_staff = True
        viewer.save()
        self.assertTrue(self.client.login(username="viewer", password="pw"))

    def test_machine_detail_shows_buttons_and_counts(self):
        HealthReport.objects.create(
            hostname="alert01", family=4,
            summary={
                "info": {
                    "needs_reboot": True,
                    "ubuntu_updates": 7,
                    "ubuntu_security_updates": 2,
                },
                "health": {},
            },
        )
        r = self.client.get("/machines/alert01/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "reboot required")
        self.assertContains(r, "7 updates")
        self.assertContains(r, "2 security updates")

    def test_machine_detail_no_alerts_when_clean(self):
        HealthReport.objects.create(
            hostname="alert01", family=4,
            summary={"info": {}, "health": {}},
        )
        r = self.client.get("/machines/alert01/")
        self.assertNotContains(r, "reboot required")
        self.assertNotContains(r, "updates")

    def test_machines_table_columns(self):
        HealthReport.objects.create(
            hostname="alert01", family=4,
            summary={
                "info": {
                    "needs_reboot": True,
                    "ubuntu_updates": 5,
                    "ubuntu_security_updates": 1,
                },
                "health": {},
            },
        )
        r = self.client.get("/machines/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, ">alert01</a>")
        self.assertNotContains(r, "alert01.ring.nlnog.net<")
        self.assertContains(r, "Datacenter")
        self.assertNotContains(r, ">IPv4</th>")
        self.assertNotContains(r, ">IPv6</th>")
        self.assertNotContains(r, "Reboot")
        self.assertNotContains(r, "Security")

    def test_index_lists_nodes_needing_attention(self):
        HealthReport.objects.create(
            hostname="alert01", family=4,
            summary={"info": {"needs_reboot": True}, "health": {}},
        )
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Machines with issues")
        self.assertContains(r, "alert01.ring.nlnog.net")

    def test_index_kpi_counts(self):
        owner2 = make_owner("alertowner2")
        Machine.objects.create(
            hostname="alert02.ring.nlnog.net", owner=owner2, autnum=124, active=1
        )
        HealthReport.objects.create(
            hostname="alert01", family=4,
            summary={
                "info": {
                    "needs_reboot": True,
                    "ubuntu_updates": 4,
                    "ubuntu_security_updates": 1,
                },
                "health": {},
            },
        )
        HealthReport.objects.create(
            hostname="alert02", family=4,
            summary={"info": {"ubuntu_updates": 2}, "health": {}},
        )
        r = self.client.get("/")
        self.assertEqual(r.context["need_reboot"], 1)
        self.assertEqual(r.context["updates_pending"], 2)
        self.assertEqual(r.context["security_updates"], 1)
        self.assertContains(r, ">all (2)<")
        self.assertContains(r, ">updates (2)<")

    def test_index_hides_clean_nodes(self):
        HealthReport.objects.create(
            hostname="alert01", family=4,
            summary={"info": {"ubuntu_release": "22.04"}, "health": {}},
        )
        r = self.client.get("/")
        self.assertContains(r, "No machines match this filter.")
        self.assertNotContains(r, "alert01.ring.nlnog.net")

    def test_index_issue_filters(self):
        nodes = {}
        for name, kw in [
            ("node01", dict(active=1, alive_v4=0, alive_v6=1)),
            ("node02", dict(active=1, alive_v4=1, alive_v6=1)),
            ("node03", dict(active=1, alive_v4=1, alive_v6=1)),
            ("node04", dict(active=0, alive_v4=1, alive_v6=1)),
        ]:
            owner = make_owner("owner-" + name)
            nodes[name] = Machine.objects.create(
                hostname=name + ".ring.nlnog.net", owner=owner, autnum=1, **kw
            )
        HealthReport.objects.create(
            hostname="node02", family=4,
            summary={"info": {"needs_reboot": True}, "health": {}},
        )
        AnsibleRun.objects.create(
            hostname="node03", failures=1, unreachable=0, ok=0, changed=0, skipped=0
        )

        r = self.client.get("/")
        for name in ("node01", "node02", "node03", "node04"):
            self.assertContains(r, name + ".ring.nlnog.net")
        self.assertContains(r, ">node01</a>")

        connectivity = self.client.get("/?issues=connectivity")
        self.assertContains(connectivity, "node01.ring.nlnog.net")
        self.assertNotContains(connectivity, "node02.ring.nlnog.net")

        updates = self.client.get("/?issues=updates")
        self.assertContains(updates, "node02.ring.nlnog.net")
        self.assertNotContains(updates, "node01.ring.nlnog.net")

        self.assertContains(updates, ">updates (1)<")
        self.assertContains(updates, ">reboot (1)<")

        reboot = self.client.get("/?issues=reboot")
        self.assertContains(reboot, "node02.ring.nlnog.net")
        self.assertNotContains(reboot, "node01.ring.nlnog.net")

        failed = self.client.get("/?issues=ansible_failed")
        self.assertContains(failed, "node03.ring.nlnog.net")
        self.assertNotContains(failed, "node01.ring.nlnog.net")

        self.assertContains(failed, ">ansible failed (1)<")

        inactive = self.client.get("/?issues=inactive")
        self.assertContains(inactive, "node04.ring.nlnog.net")
        self.assertNotContains(inactive, "node01.ring.nlnog.net")


class PeeringDBOAuthTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = Client()

    def _start_login(self):
        r = self.client.get("/accounts/peeringdb/login/")
        self.assertEqual(r.status_code, 302)
        return r.url, self.client.session["pdb_state"]

    @override_settings(**PDB_SETTINGS)
    def test_login_redirects_to_authorize(self):
        url, _ = self._start_login()
        self.assertIn("https://auth.peeringdb.com/oauth2/authorize/", url)
        self.assertIn("client_id=cid", url)

    def test_button_hidden_when_not_configured(self):
        page = self.client.get("/accounts/login/").content.decode()
        self.assertNotIn("Log in with PeeringDB", page)

    @override_settings(**PDB_SETTINGS)
    def test_button_shown_when_configured(self):
        page = self.client.get("/accounts/login/").content.decode()
        self.assertIn("Log in with PeeringDB", page)

    @override_settings(**PDB_SETTINGS)
    def test_callback_unknown_asn_queues_approval(self):
        Participant.objects.create(company="Some Corp")
        set_participant_autnum(Participant.objects.get(company="Some Corp").pk, 3333)
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=PDB_PROFILE):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Application received")
        signup = PeeringDBSignup.objects.get()
        self.assertEqual(signup.asn, 2914)
        self.assertEqual(signup.peeringdb_id, 9001)
        self.assertFalse(signup.django_user.is_active)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(**PDB_SETTINGS)
    def test_callback_known_asn_logs_in(self):
        participant = Participant.objects.create(company="Example Net")
        set_participant_autnum(participant.pk, 2914)
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=PDB_PROFILE):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertEqual(r.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        ring_user, profile = ring_user_for_pdb(9001)
        self.assertEqual(ring_user.participant, participant)
        self.assertTrue(Token.objects.filter(user=profile.django_user).exists())

    @override_settings(**PDB_SETTINGS)
    def test_callback_matches_participant_by_company(self):
        participant = Participant.objects.create(company="Example Net")
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=PDB_PROFILE):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertEqual(r.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(PeeringDBSignup.objects.count(), 0)
        ring_user, _ = ring_user_for_pdb(9001)
        self.assertEqual(ring_user.participant, participant)
        self.assertEqual(participant_autnum(participant.pk), 2914)

    @override_settings(**PDB_SETTINGS)
    def test_callback_existing_user_logs_in(self):
        participant = Participant.objects.create(company="Example Net")
        set_participant_autnum(participant.pk, 2914)
        user = User.objects.create_user(username="existing", password="pw")
        ring_user = RingUser.objects.create(
            username="existing",
            participant=participant,
            active=True,
        )
        link_ring_user(user, ring_user, peeringdb_id=9001)
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=PDB_PROFILE):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), user.pk
        )
        self.assertEqual(RingUserProfile.objects.filter(peeringdb_id=9001).count(), 1)

    @override_settings(**PDB_SETTINGS)
    def test_callback_multiple_networks_picks(self):
        p1 = Participant.objects.create(company="Example Net")
        p2 = Participant.objects.create(company="Other Net")
        set_participant_autnum(p1.pk, 2914)
        set_participant_autnum(p2.pk, 3333)
        profile = dict(PDB_PROFILE)
        profile["networks"] = [
            {"perms": 15, "asn": 2914, "name": "Example Net", "id": 99},
            {"perms": 15, "asn": 3333, "name": "Other Net", "id": 101},
        ]
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=profile):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/peeringdb/pick/", r.url)
        page = self.client.get(r.url)
        self.assertContains(page, "AS2914")
        self.assertContains(page, "AS3333")
        r2 = self.client.post(r.url, {"asn": "3333"})
        self.assertEqual(r2.status_code, 302)
        self.assertNotIn("pdb_profile", self.client.session)
        self.assertEqual(
            participant_autnum(ring_user_for_pdb(9001)[0].participant.pk), 3333
        )

    @override_settings(**PDB_SETTINGS)
    def test_callback_no_networks(self):
        profile = dict(PDB_PROFILE)
        profile["networks"] = []
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=profile):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertContains(r, "no network permissions")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(**PDB_SETTINGS)
    def test_state_mismatch_rejected(self):
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=PDB_PROFILE):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=WRONG"
            )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "state mismatch")
        self.assertEqual(PeeringDBSignup.objects.count(), 0)

    @override_settings(**PDB_SETTINGS)
    def test_approve_and_reject(self):
        make_owner("pdb-admin")
        admin = User.objects.create_superuser("pdb_admin", "a@x", "pw")
        signup = self._make_pending_signup(admin, 1)
        r = self.client.post(
            "/accounts/signups/peeringdb/%d/approve/" % signup.pk
        )
        self.assertEqual(r.status_code, 302)
        signup.refresh_from_db()
        self.assertTrue(signup.approved)
        participant = participant_for_autnum(2914)
        self.assertEqual(participant.company, "Example Net")
        ring_user, profile = ring_user_for_pdb(9001)
        self.assertEqual(ring_user.participant, participant)
        self.assertTrue(profile.django_user.is_active)
        self.assertTrue(
            Token.objects.filter(user=profile.django_user).exists()
        )

        signup2 = self._make_pending_signup(admin, 2)
        r = self.client.post(
            "/accounts/signups/peeringdb/%d/reject/" % signup2.pk
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PeeringDBSignup.objects.filter(pk=signup2.pk).count(), 0)

    @override_settings(**PDB_SETTINGS)
    def test_login_matches_machine_asn_without_backfill(self):
        existing = Participant.objects.create(company="Bitterballen")
        owner = RingUser.objects.create(
            username="bb-owner", participant=existing, active=1
        )
        Machine.objects.create(
            hostname="bb.ring.nlnog.net", owner=owner, autnum=2914, active=1
        )
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch("ring.views.fetch_profile", return_value=PDB_PROFILE):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertEqual(r.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(PeeringDBSignup.objects.count(), 0)
        ring_user, _ = ring_user_for_pdb(9001)
        self.assertEqual(ring_user.participant, existing)
        self.assertEqual(participant_autnum(existing.pk), 2914)

    @override_settings(**PDB_SETTINGS)
    def test_approve_links_existing_participant_by_company(self):
        make_owner("pdb-admin")
        User.objects.create_superuser("pdb_admin", "a@x", "pw")
        existing = Participant.objects.create(company="Bitterbal")
        signup_user = User.objects.create_user(
            username="pending-bb", email="p@x"
        )
        signup_user.is_active = False
        signup_user.save()
        signup = PeeringDBSignup.objects.create(
            django_user=signup_user,
            peeringdb_id=9102,
            peeringdb_net_id=99,
            asn=200995,
            net_name="BITTERBAL",
        )
        self.client.login(username="pdb_admin", password="pw")
        r = self.client.post(
            "/accounts/signups/peeringdb/%d/approve/" % signup.pk
        )
        self.assertEqual(r.status_code, 302)
        signup.refresh_from_db()
        self.assertTrue(signup.approved)
        self.assertEqual(
            Participant.objects.filter(company="Bitterbal").count(), 1
        )
        ring_user, profile = ring_user_for_pdb(9102)
        self.assertEqual(ring_user.participant, existing)
        self.assertTrue(profile.django_user.is_active)
        self.assertEqual(participant_autnum(existing.pk), 200995)

    @override_settings(**PDB_SETTINGS)
    def test_callback_known_asn_blocked_when_readonly(self):
        participant = Participant.objects.create(company="Example Net")
        set_participant_autnum(participant.pk, 2914)
        _, state = self._start_login()
        with mock.patch(
            "ring.views.exchange_code", return_value="tok"
        ), mock.patch(
            "ring.views.fetch_profile", return_value=PDB_PROFILE
        ), mock.patch(
            "ring.views.legacy_writable", return_value=False
        ):
            r = self.client.get(
                "/accounts/peeringdb/callback/?code=abc&state=%s" % state
            )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "read-only")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(RingUser.objects.count(), 0)
        self.assertEqual(participant_autnum(participant.pk), 2914)

    @override_settings(**PDB_SETTINGS)
    def test_approve_pdb_signup_blocked_when_readonly(self):
        make_owner("pdb-admin")
        User.objects.create_superuser("pdb_admin", "a@x", "pw")
        existing = Participant.objects.create(company="Bitterbal")
        signup_user = User.objects.create_user(
            username="pending-bb2", email="p@x"
        )
        signup_user.is_active = False
        signup_user.save()
        signup = PeeringDBSignup.objects.create(
            django_user=signup_user,
            peeringdb_id=9103,
            peeringdb_net_id=99,
            asn=200995,
            net_name="BITTERBAL",
        )
        self.client.login(username="pdb_admin", password="pw")
        with mock.patch("ring.views.legacy_writable", return_value=False):
            r = self.client.post(
                "/accounts/signups/peeringdb/%d/approve/" % signup.pk
            )
        self.assertEqual(r.status_code, 302)
        signup.refresh_from_db()
        self.assertIs(signup.approved, False)
        self.assertEqual(
            Participant.objects.filter(company="Bitterbal").count(), 1
        )
        self.assertIsNone(ring_user_for_pdb(9103)[0])

    @override_settings(**PDB_SETTINGS)
    def test_approve_pdb_signup_new_participant_blocked_when_readonly(self):
        make_owner("pdb-admin")
        admin = User.objects.create_superuser("pdb_admin", "a@x", "pw")
        signup = self._make_pending_signup(admin, 3)
        with mock.patch("ring.views.legacy_writable", return_value=False):
            r = self.client.post(
                "/accounts/signups/peeringdb/%d/approve/" % signup.pk
            )
        self.assertEqual(r.status_code, 302)
        signup.refresh_from_db()
        self.assertIs(signup.approved, False)
        self.assertEqual(
            Participant.objects.filter(company="Example Net").count(), 0
        )
        self.assertIsNone(ring_user_for_pdb(9003)[0])

    def _make_pending_signup(self, admin, salt):
        user = User.objects.create_user(
            username="pending%d-%d" % (admin.pk, salt), email="p@x"
        )
        user.is_active = False
        user.save()
        signup = PeeringDBSignup.objects.create(
            django_user=user,
            peeringdb_id=9000 + salt,
            peeringdb_net_id=99,
            asn=2914,
            net_name="Example Net",
        )
        self.client.logout()
        self.client.login(username="pdb_admin", password="pw")
        return signup


class BackfillAsnTest(TestCase):
    def test_single_asn_backfilled(self):
        from django.core.management import call_command

        owner = make_owner("bf1")
        Machine.objects.create(
            hostname="a.ring.nlnog.net", owner=owner, autnum=111, active=1
        )
        Machine.objects.create(
            hostname="b.ring.nlnog.net", owner=owner, autnum=111, active=1
        )
        call_command("ring_backfill_asn", verbosity=0)
        self.assertEqual(participant_autnum(owner.participant.pk), 111)

    def test_multi_asn_uses_mode(self):
        from django.core.management import call_command

        owner = make_owner("bf2")
        Machine.objects.create(
            hostname="a.ring.nlnog.net", owner=owner, autnum=111, active=1
        )
        Machine.objects.create(
            hostname="b.ring.nlnog.net", owner=owner, autnum=111, active=1
        )
        Machine.objects.create(
            hostname="c.ring.nlnog.net", owner=owner, autnum=222, active=1
        )
        call_command("ring_backfill_asn", verbosity=0)
        self.assertEqual(participant_autnum(owner.participant.pk), 111)

    def test_existing_autnum_not_overwritten(self):
        from django.core.management import call_command

        owner = make_owner("bf3")
        set_participant_autnum(owner.participant.pk, 555)
        Machine.objects.create(
            hostname="a.ring.nlnog.net", owner=owner, autnum=111, active=1
        )
        call_command("ring_backfill_asn", verbosity=0)
        self.assertEqual(participant_autnum(owner.participant.pk), 555)

    def test_shared_asn_reported_not_crashed(self):
        from django.core.management import call_command

        owner_a = make_owner("bf4a")
        owner_b = make_owner("bf4b")
        Machine.objects.create(
            hostname="a.ring.nlnog.net", owner=owner_a, autnum=999, active=1
        )
        Machine.objects.create(
            hostname="b.ring.nlnog.net", owner=owner_b, autnum=999, active=1
        )
        out = StringIO()
        call_command("ring_backfill_asn", stdout=out)
        owner_ids = [owner_a.participant.pk, owner_b.participant.pk]
        assigned = [
            participant_autnum(pk) for pk in owner_ids
        ]
        self.assertEqual(assigned.count(999), 1)
        self.assertEqual(assigned.count(None), 1)
        self.assertIn("already assigned to another participant", out.getvalue())
