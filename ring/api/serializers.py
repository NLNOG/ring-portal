"""DRF serializers for the ring domain."""

from rest_framework import serializers

from ring.api.permissions import is_ring_admin, org_of_user
from ring.models import (
    AnsibleRun,
    HealthReport,
    Machine,
    MachineRemark,
    Participant,
    ParticipantRemark,
    RingUser,
    SSHHostKey,
    SSHKey,
)


def _admin(request):
    return request is not None and is_ring_admin(request.user)


def _org(request):
    return None if request is None else org_of_user(request.user)


class ParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Participant
        fields = [
            "id",
            "company",
            "url",
            "contact",
            "email",
            "nocemail",
            "companydesc",
            "public",
            "tstamp",
        ]
        read_only_fields = ["tstamp"]


class RingUserSerializer(serializers.ModelSerializer):
    username = serializers.CharField()
    machines = serializers.SlugRelatedField(
        many=True, read_only=True, slug_field="hostname"
    )

    class Meta:
        model = RingUser
        fields = [
            "id",
            "username",
            "userid",
            "shell",
            "active",
            "participant",
            "admin",
            "email",
            "machines",
        ]

    def validate_participant(self, value):
        request = self.context.get("request")
        if not _admin(request) and value != _org(request):
            raise serializers.ValidationError(
                "You may only write objects of your own participant."
            )
        return value

    def validate_admin(self, value):
        request = self.context.get("request")
        if value and not _admin(request):
            raise serializers.ValidationError(
                "Only ring administrators can grant the admin flag."
            )
        return value


class MachineSerializer(serializers.ModelSerializer):
    short_hostname = serializers.CharField(read_only=True)

    class Meta:
        model = Machine
        fields = [
            "id",
            "hostname",
            "short_hostname",
            "v4",
            "v6",
            "autnum",
            "country",
            "state",
            "city",
            "dc",
            "geo",
            "owner",
            "tstamp",
            "active",
            "alive_v4",
            "alive_v6",
            "last_active",
        ]
        read_only_fields = ["tstamp", "last_active", "alive_v4", "alive_v6"]

    def validate_owner(self, value):
        request = self.context.get("request")
        if _admin(request):
            return value
        org = _org(request)
        if value is None or org is None or value.participant_id != org.pk:
            raise serializers.ValidationError(
                "Owner must be a user of your own participant."
            )
        return value


class SSHKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = SSHKey
        fields = ["id", "keytype", "sshkey", "keyid", "user"]

    def validate_user(self, value):
        request = self.context.get("request")
        if _admin(request):
            return value
        org = _org(request)
        if value is None or org is None or value.participant_id != org.pk:
            raise serializers.ValidationError(
                "User must belong to your own participant."
            )
        return value


class SSHHostKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = SSHHostKey
        fields = ["id", "keytype", "sshkey", "keyid", "machine"]

    def validate_machine(self, value):
        request = self.context.get("request")
        if _admin(request):
            return value
        org = _org(request)
        if value is None or org is None or value.owner.participant_id != org.pk:
            raise serializers.ValidationError(
                "Machine must be owned by your own participant."
            )
        return value


class ParticipantRemarkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParticipantRemark
        fields = ["id", "remark", "tstamp", "participant"]

    def validate_participant(self, value):
        request = self.context.get("request")
        if not _admin(request) and value != _org(request):
            raise serializers.ValidationError(
                "You may only write remarks for your own participant."
            )
        return value


class MachineRemarkSerializer(serializers.ModelSerializer):
    class Meta:
        model = MachineRemark
        fields = ["id", "remark", "tstamp", "machine"]

    def validate_machine(self, value):
        request = self.context.get("request")
        if _admin(request):
            return value
        org = _org(request)
        if value is None or org is None or value.owner.participant_id != org.pk:
            raise serializers.ValidationError(
                "Machine must be owned by your own participant."
            )
        return value


class AnsibleRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnsibleRun
        fields = [
            "id",
            "timestamp",
            "hostname",
            "unreachable",
            "ok",
            "changed",
            "skipped",
            "failures",
        ]


class HealthReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthReport
        fields = ["id", "timestamp", "hostname", "family", "summary"]
