; 错误：自应用导致无限类型 'a = ('a -> 'b)，occurs check 拒绝
(def w (fn x (x x)))
