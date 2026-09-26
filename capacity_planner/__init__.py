"""资源容量规划计算库：排队论模型 + 内置模拟校准 + 配额建议。"""
from .models import (ModelError, QueueMetrics, mmc_metrics, mgc_metrics,
                     erlang_b, erlang_c, p_wait_exceeds, mmck_metrics,
                     burst_overload)
from .simulate import SimConfig, SimResult, run_simulation
from .calibrate import calibrate, CalibrationReport
from .recommend import recommend, Recommendation

__version__ = "0.1.0"
