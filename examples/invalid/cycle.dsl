step build {
  needs: test;
}
step test {
  needs: build;
}
