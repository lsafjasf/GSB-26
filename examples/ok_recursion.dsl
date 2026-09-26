; 递归与相互递归
(defrec (fact (fn n (if (= n 0) 1 (* n (fact (- n 1)))))))
(defrec
  (even? (fn n (if (= n 0) true  (odd?  (- n 1)))))
  (odd?  (fn n (if (= n 0) false (even? (- n 1))))))
(def f5 (fact 5))
(def e10 (even? 10))
(defrec (len (fn xs (if (nil? xs) 0 (+ 1 (len (tail xs)))))))
(def l (len (list 1 2 3)))
