# 判定来源追踪报告

共 4 次判定。

## 判定 1

输入: level='member', amount=1500, weight=40, region='overseas', fragile=False
评估过程（按优先级降序）:
  [R01] (priority=100) 未命中
      首个不满足条件: ✗ region != 'overseas'（实际值 'overseas'）
  [R02] (priority=90) 未命中
      首个不满足条件: ✗ region != 'overseas'（实际值 'overseas'）
  [R03] (priority=85) 未命中
      首个不满足条件: ✗ level == 'vip'（实际值 'member'）
  [R04] (priority=84) 未命中
      首个不满足条件: ✗ level == 'vip'（实际值 'member'）
  [R05] (priority=83) 命中
      ✓ region == 'overseas'（实际值 'overseas'）
      ✓ level != 'vip'（实际值 'member'）
      ✓ weight > 30（实际值 40）
  [R06] (priority=82) 被优先级跳过
      条件全部满足，但优先级低于 R05，被跳过
  [R07] (priority=81) 被优先级跳过
      条件全部满足，但优先级低于 R05，被跳过
  …其余 9 条规则排在命中规则之后，未参与评估
结论: 规则 [R05] 命中 -> ('reject', 0.0)；R06, R07 条件同样满足但被优先级跳过

## 判定 2

输入: level='vip', amount=500, weight=20, region='remote', fragile=False
评估过程（按优先级降序）:
  [R01] (priority=100) 未命中
      首个不满足条件: ✗ fragile == True（实际值 False）
  [R02] (priority=90) 未命中
      首个不满足条件: ✗ fragile == True（实际值 False）
  [R03] (priority=85) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'remote'）
  [R04] (priority=84) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'remote'）
  [R05] (priority=83) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'remote'）
  [R06] (priority=82) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'remote'）
  [R07] (priority=81) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'remote'）
  [R08] (priority=70) 命中
      ✓ region == 'remote'（实际值 'remote'）
      ✓ fragile == False（实际值 False）
      ✓ level == 'vip'（实际值 'vip'）
      ✓ amount >= 500（实际值 500）
  [R09] (priority=69) 被优先级跳过
      条件全部满足，但优先级低于 R08，被跳过
  [R10] (priority=68) 被优先级跳过
      条件全部满足，但优先级低于 R08，被跳过
  …其余 6 条规则排在命中规则之后，未参与评估
结论: 规则 [R08] 命中 -> ('express', 0.0)；R09, R10 条件同样满足但被优先级跳过

## 判定 3

输入: level='member', amount=100, weight=12, region='local', fragile=True
评估过程（按优先级降序）:
  [R01] (priority=100) 命中
      ✓ region != 'overseas'（实际值 'local'）
      ✓ fragile == True（实际值 True）
      ✓ weight > 10（实际值 12）
  [R02] (priority=90) 被优先级跳过
      条件全部满足，但优先级低于 R01，被跳过
  …其余 14 条规则排在命中规则之后，未参与评估
结论: 规则 [R01] 命中 -> ('freight', 104.0)；R02 条件同样满足但被优先级跳过

## 判定 4

输入: level='guest', amount=100, weight=3, region='local', fragile=False
评估过程（按优先级降序）:
  [R01] (priority=100) 未命中
      首个不满足条件: ✗ fragile == True（实际值 False）
  [R02] (priority=90) 未命中
      首个不满足条件: ✗ fragile == True（实际值 False）
  [R03] (priority=85) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'local'）
  [R04] (priority=84) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'local'）
  [R05] (priority=83) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'local'）
  [R06] (priority=82) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'local'）
  [R07] (priority=81) 未命中
      首个不满足条件: ✗ region == 'overseas'（实际值 'local'）
  [R08] (priority=70) 未命中
      首个不满足条件: ✗ region == 'remote'（实际值 'local'）
  [R09] (priority=69) 未命中
      首个不满足条件: ✗ region == 'remote'（实际值 'local'）
  [R10] (priority=68) 未命中
      首个不满足条件: ✗ region == 'remote'（实际值 'local'）
  [R11] (priority=60) 未命中
      首个不满足条件: ✗ level == 'vip'（实际值 'guest'）
  [R12] (priority=59) 未命中
      首个不满足条件: ✗ level == 'vip'（实际值 'guest'）
  [R13] (priority=58) 未命中
      首个不满足条件: ✗ level == 'member'（实际值 'guest'）
  [R14] (priority=57) 未命中
      首个不满足条件: ✗ level == 'member'（实际值 'guest'）
  [R15] (priority=56) 未命中
      首个不满足条件: ✗ amount >= 500（实际值 100）
  [R16] (priority=55) 未命中
      首个不满足条件: ✗ weight > 5（实际值 3）
结论: 默认分支 [DEFAULT] 生效 -> ('standard', 8.0)
默认分支生效原因: 16 条业务规则均未命中（见上）
