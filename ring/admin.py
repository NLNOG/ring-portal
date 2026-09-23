from django.contrib import admin

from .models import (
    AnsibleRun,
    HealthReport,
    Machine,
    MachineRemark,
    Participant,
    ParticipantProfile,
    ParticipantRemark,
    PeeringDBSignup,
    RingSignup,
    RingUser,
    RingUserProfile,
    SSHHostKey,
    SSHKey,
)
from ring.services.profiles import participant_autnum, profile_for_ring_user


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ["id", "company", "autnum", "url", "contact", "email", "nocemail", "public"]
    search_fields = ["company", "contact", "email"]

    @admin.display(description="ASN")
    def autnum(self, participant):
        return participant_autnum(participant.pk) or "—"


@admin.register(RingUser)
class RingUserAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "username",
        "linked_user",
        "userid",
        "participant",
        "active",
        "admin",
        "pdb_id",
    ]
    search_fields = ["username", "email"]
    list_filter = ["active", "admin"]

    @admin.display(description="django user")
    def linked_user(self, obj):
        profile = profile_for_ring_user(obj.pk)
        return profile.django_user.username if profile else None

    @admin.display(description="peeringdb id")
    def pdb_id(self, obj):
        profile = profile_for_ring_user(obj.pk)
        return profile.peeringdb_id if profile else None


@admin.register(RingSignup)
class RingSignupAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "django_user",
        "company",
        "ring_username",
        "created",
        "approved",
        "approved_at",
    ]
    search_fields = ["django_user__username", "participant_id"]
    list_filter = ["approved"]


@admin.register(PeeringDBSignup)
class PeeringDBSignupAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "django_user",
        "asn",
        "peeringdb_net_id",
        "net_name",
        "created",
        "approved",
        "approved_at",
    ]
    search_fields = ["django_user__username", "net_name"]
    list_filter = ["approved"]


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "hostname",
        "owner",
        "country",
        "city",
        "active",
        "alive_v4",
        "alive_v6",
        "last_active",
    ]
    search_fields = ["hostname", "city"]
    list_filter = ["country", "active", "alive_v4", "alive_v6"]


@admin.register(SSHKey)
class SSHKeyAdmin(admin.ModelAdmin):
    list_display = ["id", "keytype", "keyid", "user"]
    search_fields = ["keyid"]


@admin.register(SSHHostKey)
class SSHHostKeyAdmin(admin.ModelAdmin):
    list_display = ["id", "keytype", "keyid", "machine"]
    search_fields = ["keyid", "machine__hostname"]


@admin.register(ParticipantRemark)
class ParticipantRemarkAdmin(admin.ModelAdmin):
    list_display = ["id", "participant", "tstamp"]


@admin.register(MachineRemark)
class MachineRemarkAdmin(admin.ModelAdmin):
    list_display = ["id", "machine", "tstamp"]


@admin.register(AnsibleRun)
class AnsibleRunAdmin(admin.ModelAdmin):
    list_display = ["id", "hostname", "timestamp", "unreachable", "failures", "ok"]
    search_fields = ["hostname"]


@admin.register(HealthReport)
class HealthReportAdmin(admin.ModelAdmin):
    list_display = ["id", "hostname", "family", "timestamp"]
    search_fields = ["hostname"]


@admin.register(RingUserProfile)
class RingUserProfileAdmin(admin.ModelAdmin):
    list_display = ["id", "django_user", "ring_user_id", "peeringdb_id", "peeringdb_net_id"]
    search_fields = ["django_user__username", "ring_user_id", "peeringdb_id"]


@admin.register(ParticipantProfile)
class ParticipantProfileAdmin(admin.ModelAdmin):
    list_display = ["id", "participant_id", "autnum"]
    search_fields = ["participant_id", "autnum"]