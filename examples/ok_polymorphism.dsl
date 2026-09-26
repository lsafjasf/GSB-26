; 多态：id 先后作用于 Int 与 Bool；cons/head 泛型实例化
(def id (fn x x))
(def a (id 1))
(def b (id true))
(def nums (cons 1 (cons 2 nil)))
(def flags (cons true nil))
(def first-num (head nums))
(def apply2 (fn f (fn x (f (f x)))))
(def plus2 (apply2 (fn n (+ n 1))))
