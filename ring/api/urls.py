from django.urls import include, path
from rest_framework.routers import DefaultRouter

from ring.api import views, viewsets

router = DefaultRouter()
router.register(r"participants", viewsets.ParticipantViewSet)
router.register(r"users", viewsets.RingUserViewSet)
router.register(r"machines", viewsets.MachineViewSet)
router.register(r"sshkeys", viewsets.SSHKeyViewSet)
router.register(r"sshhostkeys", viewsets.SSHHostKeyViewSet)
router.register(r"premarks", viewsets.ParticipantRemarkViewSet)
router.register(r"mremarks", viewsets.MachineRemarkViewSet)
router.register(r"ansible", viewsets.AnsibleRunViewSet, basename="ansible")
router.register(r"health", viewsets.HealthReportViewSet, basename="health")

urlpatterns = [
    path("", include(router.urls)),
    path("api-auth/", include("rest_framework.urls")),
    path("account/me/", views.AccountMeView.as_view(), name="api-account-me"),
    path("account/login/", views.ApiLoginView.as_view(), name="api-account-login"),
]
