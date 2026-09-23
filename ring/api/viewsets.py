"""DRF viewsets for the ring domain.

Read/write viewsets for core domain tables and POST-only ingest endpoints for
the high-volume status tables (ansible runs, health reports) that nodes push.
"""

from django.db import IntegrityError, transaction
from rest_framework import filters, viewsets, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from ring.api.permissions import (
    IngestPermission,
    MachinePermission,
    MachineRemarkPermission,
    ParticipantPermission,
    ParticipantRemarkPermission,
    RingUserPermission,
    SSHHostKeyPermission,
    SSHKeyPermission,
    is_ring_admin,
)
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
from ring.api.serializers import (
    AnsibleRunSerializer,
    HealthReportSerializer,
    MachineRemarkSerializer,
    MachineSerializer,
    ParticipantRemarkSerializer,
    ParticipantSerializer,
    RingUserSerializer,
    SSHHostKeySerializer,
    SSHKeySerializer,
)
from ring.services.profiles import legacy_writable

_READONLY_MSG = (
    "The legacy database is read-only in this deployment; this operation is disabled."
)


class SafeDestroyMixin:
    """Turn DB RESTRICT/FK errors on delete into a clean 400 instead of 500."""

    def perform_destroy(self, instance):
        if not legacy_writable():
            raise PermissionDenied(_READONLY_MSG)
        try:
            instance.delete()
        except IntegrityError:
            raise ValidationError(
                "Cannot delete: this record is still referenced by other objects."
            )


class LegacyWritableMixin:
    """Block Group A create/update when the legacy source is read-only."""

    def perform_create(self, serializer):
        if not legacy_writable():
            raise PermissionDenied(_READONLY_MSG)
        return serializer.save()

    def perform_update(self, serializer):
        if not legacy_writable():
            raise PermissionDenied(_READONLY_MSG)
        return serializer.save()


class ParticipantViewSet(LegacyWritableMixin, viewsets.ModelViewSet):
    queryset = Participant.objects.select_related().all()
    serializer_class = ParticipantSerializer
    permission_classes = [ParticipantPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["company", "companydesc", "contact", "email"]
    ordering_fields = "__all__"

    def perform_create(self, serializer):
        if not is_ring_admin(self.request.user):
            raise ValidationError(
                "Only ring administrators can create participants."
            )
        super().perform_create(serializer)


class RingUserViewSet(LegacyWritableMixin, SafeDestroyMixin, viewsets.ModelViewSet):
    queryset = RingUser.objects.select_related("participant").all()
    serializer_class = RingUserSerializer
    permission_classes = [RingUserPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "email"]
    ordering_fields = "__all__"


class MachineViewSet(LegacyWritableMixin, SafeDestroyMixin, viewsets.ModelViewSet):
    queryset = Machine.objects.select_related("owner").all()
    serializer_class = MachineSerializer
    permission_classes = [MachinePermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["hostname", "city", "country", "state", "dc"]
    ordering_fields = "__all__"


class SSHKeyViewSet(LegacyWritableMixin, SafeDestroyMixin, viewsets.ModelViewSet):
    queryset = SSHKey.objects.select_related("user").all()
    serializer_class = SSHKeySerializer
    permission_classes = [SSHKeyPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["keytype", "keyid", "sshkey"]
    ordering_fields = "__all__"


class SSHHostKeyViewSet(LegacyWritableMixin, SafeDestroyMixin, viewsets.ModelViewSet):
    queryset = SSHHostKey.objects.select_related("machine").all()
    serializer_class = SSHHostKeySerializer
    permission_classes = [SSHHostKeyPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["keytype", "keyid", "sshkey"]
    ordering_fields = "__all__"


class ParticipantRemarkViewSet(LegacyWritableMixin, SafeDestroyMixin, viewsets.ModelViewSet):
    queryset = ParticipantRemark.objects.select_related("participant").all()
    serializer_class = ParticipantRemarkSerializer
    permission_classes = [ParticipantRemarkPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["remark"]
    ordering_fields = "__all__"


class MachineRemarkViewSet(LegacyWritableMixin, SafeDestroyMixin, viewsets.ModelViewSet):
    queryset = MachineRemark.objects.select_related("machine").all()
    serializer_class = MachineRemarkSerializer
    permission_classes = [MachineRemarkPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["remark"]
    ordering_fields = "__all__"


class _IngestViewSet(viewsets.GenericViewSet):
    """POST-only ingest base; nodes authenticate with a token and push runs."""

    permission_classes = [IngestPermission]

    def create(self, request, *args, **kwargs):
        if not legacy_writable():
            raise PermissionDenied(_READONLY_MSG)
        serializer = self.get_serializer(data=request.data, many=self._is_list(request))
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _is_list(request):
        return isinstance(request.data, list)


class AnsibleRunViewSet(_IngestViewSet):
    serializer_class = AnsibleRunSerializer
    queryset = AnsibleRun.objects.all()

    def perform_create(self, serializer):
        serializer.save()


class HealthReportViewSet(_IngestViewSet):
    serializer_class = HealthReportSerializer
    queryset = HealthReport.objects.all()

    def perform_create(self, serializer):
        serializer.save()
