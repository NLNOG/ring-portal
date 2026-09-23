"""Account endpoints: the current caller's profile + username/password login."""

from django.contrib.auth import authenticate
from rest_framework import permissions
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView

from ring.api.permissions import is_ring_admin
from ring.services.profiles import ring_user


def _me_payload(user):
    token, _ = Token.objects.get_or_create(user=user)
    ru = ring_user(user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_staff": user.is_staff,
        "is_ring_admin": is_ring_admin(user),
        "active": user.is_active,
        "token": token.key,
        "participant": ru.participant_id if ru else None,
        "ring_username": ru.username if ru else None,
    }


class AccountMeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(_me_payload(request.user))


class ApiLoginView(APIView):
    permission_classes = []

    def post(self, request):
        user = authenticate(
            request,
            username=request.data.get("username", ""),
            password=request.data.get("password", ""),
        )
        if user is None:
            raise AuthenticationFailed("Invalid username or password.")
        return Response(_me_payload(user))