from django.contrib.auth import views as auth_views
from django.urls import path

from ring import views

urlpatterns = [
    path("", views.index, name="ring-index"),
    path("my/", views.my_portal, name="ring-my"),
    path("machines/", views.machines, name="ring-machines"),
    path("machines/<str:hostname>/", views.machine_detail, name="ring-machine-detail"),
    path(
        "machines/<str:hostname>/status.json/",
        views.machine_status,
        name="ring-machine-status",
    ),
    path("participants/", views.participants, name="ring-participants"),
    path("users/", views.users, name="ring-users"),
    path(
        "participants/<int:pk>/info.json/",
        views.participant_info,
        name="ring-participant-info",
    ),
    path(
        "participants/<int:pk>/edit/",
        views.participant_edit,
        name="ring-participant-edit",
    ),
    path(
        "participants/<int:pk>/switch/",
        views.participant_switch,
        name="ring-participant-switch",
    ),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="ring/login.html"),
        name="ring-login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(),
        name="ring-logout",
    ),
    path("accounts/signup/", views.signup, name="ring-signup"),
    path("accounts/signups/", views.signups, name="ring-signups"),
    path(
        "accounts/signups/<int:pk>/approve/",
        views.approve_signup,
        name="ring-signup-approve",
    ),
    path(
        "accounts/signups/<int:pk>/reject/",
        views.reject_signup,
        name="ring-signup-reject",
    ),
    path(
        "accounts/peeringdb/login/",
        views.peeringdb_login,
        name="ring-pdb-login",
    ),
    path(
        "accounts/peeringdb/callback/",
        views.peeringdb_callback,
        name="ring-pdb-callback",
    ),
    path(
        "accounts/peeringdb/pick/",
        views.peeringdb_pick,
        name="ring-pdb-pick",
    ),
    path(
        "accounts/signups/peeringdb/<int:pk>/approve/",
        views.approve_peeringdb_signup,
        name="ring-pdb-signup-approve",
    ),
    path(
        "accounts/signups/peeringdb/<int:pk>/reject/",
        views.reject_peeringdb_signup,
        name="ring-pdb-signup-reject",
    ),
]