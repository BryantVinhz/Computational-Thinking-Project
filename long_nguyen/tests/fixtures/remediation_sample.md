# SQL Injection Prevention
### Skipped Level Sub-topic {#skipped-anchor}
Here is a quick note on skipped heading levels.

## Anatomy of Vulnerability
The following example is vulnerable to SQL injection:
```java
String query = "SELECT * FROM users WHERE name = " + input;
```

## Primary Defenses
### Defense Option 1: Parameterized Queries
Rule 1: MUST always use prepared statements with parameterized queries.
#### Safe Java Example
This recommended implementation prevents injection:
```c#
PreparedStatement stmt = conn.prepareStatement("SELECT * FROM users WHERE name = ?");
stmt.setString(1, input);
```
