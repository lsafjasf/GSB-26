"""重构后：服务层包装下游错误时保留完整原因链（raise ... from ...）。"""

from . import cache, net, store
from .errors import AppError


def load_profile(user_id, *, transport, dial, backend):
    try:
        raw = net.http_get("http://api.internal/users/%s" % user_id,
                           transport=transport)
        conn = store.connect("db://main", dial=dial)
        cached = cache.get("profile:%s" % user_id, backend=backend)
    except AppError as exc:
        raise AppError("PROFILE_LOAD_FAILED", cause=exc,
                       user_id=user_id) from exc
    return {"user_id": user_id, "raw": raw, "cached": cached}
