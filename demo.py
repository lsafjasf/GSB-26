"""numdiff 快速演示。"""
import math
from numdiff import adaptive_derivative, differentiate_grid

r = adaptive_derivative(math.sin, 1.0, order=1)
print(f"sin'(1)  = {r.value:.12f}  估计误差 {r.error_est:.1e}  "
      f"实际误差 {abs(r.value - math.cos(1.0)):.1e}  h={r.h:.1e}  求值 {r.n_evals} 次")

r = adaptive_derivative(math.sin, 1.0, order=2)
print(f"sin''(1) = {r.value:.12f}  估计误差 {r.error_est:.1e}  "
      f"实际误差 {abs(r.value + math.sin(1.0)):.1e}  h={r.h:.1e}  求值 {r.n_evals} 次")

xs = [i / 10 for i in range(11)]
ys = [math.exp(x) for x in xs]
d = differentiate_grid(xs, ys, m=1)
print(f"exp'(0)≈{d[0]:.8f} (边界, 真值 1)   exp'(1)≈{d[-1]:.8f} (边界, 真值 e)")
