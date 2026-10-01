"""Fail-closed visibility gate for heroes sent to the external overlay."""


def overlay_hero_visible(hero):
    """Pass visible opponents only; the local hero may remain available off-screen."""
    if hero.get("illusion"):
        return False
    local = bool(hero.get("is_local"))
    if not local and (hero.get("hide") or hero.get("fog") or not hero.get("screen")):
        return False
    return True
