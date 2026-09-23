from ring.services.profiles import ring_user


def user_can_manage(user):
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    ru = ring_user(user)
    return bool(ru and ru.is_ring_admin)


def auth_context(request):
    from django.conf import settings

    return {
        "user_can_manage": user_can_manage(request.user),
        "pdb_enabled": bool(settings.PDB_CLIENT_ID and settings.PDB_REDIRECT_URL),
    }