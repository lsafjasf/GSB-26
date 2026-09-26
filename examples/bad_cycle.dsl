step a {
  needs: c
  run: "a"
}

step b {
  needs: a
  run: "b"
}

step c {
  needs: b
  run: "c"
}
