from apps.accounts.models import User


def recipient(n):
    """Stable test user for the integer labels the dispatch tests already use."""
    email = f"notify-user-{n}@example.com"
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(email=email)
    return user
