; 错误：把 Int 传给标注为 Bool 的参数
(def f (fn (x : Bool) x))
(def y (f 1))
