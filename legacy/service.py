"""重构前：服务层把下游异常 str() 后包一层，类型与原因链全部丢失。"""

from . import cache, net, store


class ServiceError(Exception):
    pass


def load_profile(user_id, *, transport, dial, backend):
    try:
        raw = net.http_get("http://api.internal/users/%s" % user_id,
                           transport=transport)
    except net.NetError as exc:
        raise ServiceError("profile fetch failed for user %s: %s"
                           % (user_id, exc))
    try:
        conn = store.connect("db://main", dial=dial)
    except store.StoreError as exc:
        raise ServiceError("profile fetch failed for user %s: %s"
                           % (user_id, exc))
    try:
        cached = cache.get("profile:%s" % user_id, backend=backend)
    except cache.CacheDown as exc:
        raise ServiceError("profile fetch failed for user %s: %s"
                           % (user_id, exc))
    return {"user_id": user_id, "raw": raw, "cached": cached}
