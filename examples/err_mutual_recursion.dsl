; 错误：相互递归组内的类型冲突（is-zero? 期望 Int，收到标注为 Bool 的参数）
(defrec
  (is-zero? (fn n (= n 0)))
  (flip     (fn (b : Bool) (is-zero? b))))
