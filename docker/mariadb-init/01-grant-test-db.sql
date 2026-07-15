-- Django's test runner creates/drops a `test_<DB_NAME>` database around test
-- runs, so the app user needs privileges beyond just its own database.
GRANT ALL PRIVILEGES ON `test\_%`.* TO 'quizz'@'%';
FLUSH PRIVILEGES;
