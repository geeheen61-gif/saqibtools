<?php
/**
 * launch_download.php - Generates a self-contained .bat launcher file.
 * The Python script is embedded as base64 so NO separate download needed.
 */
if (session_status() === PHP_SESSION_NONE) session_start();
if (!isset($_SESSION['uid'])) { header("Location: login.php"); exit(); }

require_once 'db_config.php';

$token = isset($_GET['token']) ? trim($_GET['token']) : '';
if (empty($token)) { header("Location: user_dashboard.php"); exit(); }

try {
    $pdo = get_db_connection();
    $stmt = $pdo->prepare("SELECT tool_name, url, cookies FROM launch_tokens WHERE token = :token LIMIT 1");
    $stmt->execute(['token' => $token]);
    $lt = $stmt->fetch();
    if (!$lt) { http_response_code(404); echo 'Invalid token.'; exit(); }

    $tool_name  = $lt['tool_name'];
    $base       = (isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] === 'on' ? 'https' : 'http') . '://' . $_SERVER['HTTP_HOST'] . rtrim(dirname($_SERVER['SCRIPT_NAME']), '/\\');
    $safe_name  = preg_replace('/[\/\\\\]/', '_', str_replace(' ', '_', $tool_name));

    // === EMBEDDED PYTHON LAUNCHER (base64) ===
    $b64 = "IiIiClNhcWliIFRvb2xzIC0gU2lsZW50IExhdW5jaGVyClJ1bnMgd2l0aCBweXRob253LmV4ZSAobm8gY29uc29sZSkuIFVzZXJzIHNlZSBOT1RISU5HIGV4Y2VwdCB0aGUgYnJvd3Nlci4KRXJyb3IgbWVzc2FnZXMgYXBwZWFyIGFzIFdpbmRvd3MgbWVzc2FnZSBib3hlcy4KIiIiCmltcG9ydCBzeXMsIGpzb24sIHVybGxpYi5yZXF1ZXN0LCBzdWJwcm9jZXNzLCBvcywgdGVtcGZpbGUsIHNodXRpbCwgY3R5cGVzLCBpbXBvcnRsaWIKCkJBU0UgPSBvcy5wYXRoLmRpcm5hbWUob3MucGF0aC5hYnNwYXRoKF9fZmlsZV9fKSkKc3AgPSBvcy5wYXRoLmpvaW4oQkFTRSwgInB5dGhvbiIsICJMaWIiLCAic2l0ZS1wYWNrYWdlcyIpCmlmIG9zLnBhdGguaXNkaXIoc3ApOgogICAgc3lzLnBhdGguaW5zZXJ0KDAsIHNwKQoKTUJfT0sgPSAwCk1CX0lDT05FUlJPUiA9IDE2Ck1CX0lDT05JTkZPID0gNjQKCmRlZiBtc2dib3godGV4dCwgdGl0bGU9IlNhcWliIFRvb2xzIiwgZmxhZ3M9TUJfT0sgfCBNQl9JQ09ORVJST1IpOgogICAgdHJ5OiBjdHlwZXMud2luZGxsLnVzZXIzMi5NZXNzYWdlQm94VygwLCB0ZXh0LCB0aXRsZSwgZmxhZ3MpCiAgICBleGNlcHQ6IHBhc3MKCkFOVElfVEhFRlRfSlMgPSAiIiIKKGZ1bmN0aW9uKCl7CiAgICBkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCdjb250ZXh0bWVudScsZnVuY3Rpb24oZSl7ZS5wcmV2ZW50RGVmYXVsdCgpfSk7CiAgICBkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCdrZXlkb3duJyxmdW5jdGlvbihlKXsKICAgICAgICB2YXIgaz1lLmtleS50b1VwcGVyQ2FzZSgpOwogICAgICAgIGlmKGs9PT0nRjEyJ3x8KGUuY3RybEtleSYmKGs9PT0nVSd8fGs9PT0nUyd8fGs9PT0nQycpKXx8KGUuY3RybEtleSYmZS5zaGlmdEtleSYmWydJJywnSicsJ0MnLCdLJ10uaW5jbHVkZXMoaykpKXtlLnByZXZlbnREZWZhdWx0KCk7cmV0dXJuIGZhbHNlfQogICAgfSk7CiAgICBbJ2xvZycsJ3dhcm4nLCdlcnJvcicsJ2luZm8nLCdkZWJ1ZycsJ3RhYmxlJywnZGlyJywndHJhY2UnXS5mb3JFYWNoKGZ1bmN0aW9uKG0pe3RyeXt3aW5kb3cuY29uc29sZVttXT1mdW5jdGlvbigpe319Y2F0Y2goZSl7fX0pOwp9KSgpOwoiIiIKClNFTVJVU0hfSlMgPSAiIiIKKGZ1bmN0aW9uKCl7CiAgICB2YXIgcz1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCdzdHlsZScpOwogICAgcy50ZXh0Q29udGVudD0nI3NyZi1oZWFkZXI+ZGl2PmRpdi5zcmYtaGVhZGVyX19lbmQ+bmF2LC5zcmYtdXBncmFkZS1iYW5uZXIsLnNyZi1wcm9tb3tkaXNwbGF5Om5vbmUhaW1wb3J0YW50fSc7CiAgICBkb2N1bWVudC5oZWFkLmFwcGVuZENoaWxkKHMpOwogICAgdmFyIHQ9c2V0SW50ZXJ2YWwoZnVuY3Rpb24oKXsKICAgICAgICB2YXIgZT1kb2N1bWVudC5xdWVyeVNlbGVjdG9yKCcjc3JmLWhlYWRlcj5kaXY+ZGl2LnNyZi1oZWFkZXJfX2VuZD5uYXYsLnNyZi11cGdyYWRlLWJhbm5lciwuc3JmLXByb21vJyk7CiAgICAgICAgaWYoZSl7ZS5zdHlsZS5kaXNwbGF5PSdub25lJztjbGVhckludGVydmFsKHQpfQogICAgfSw1MDApOwp9KSgpOwoiIiIKCgpkZWYgbWFpbigpOgogICAgdHJ5OiBjdHlwZXMud2luZGxsLnVzZXIzMi5TaG93V2luZG93KGN0eXBlcy53aW5kbGwua2VybmVsMzIuR2V0Q29uc29sZVdpbmRvdygpLCAwKQogICAgZXhjZXB0OiBwYXNzCgogICAgaWYgbGVuKHN5cy5hcmd2KSA8IDI6CiAgICAgICAgbXNnYm94KCJNaXNzaW5nIGxhdW5jaCB0b2tlbi4iLCAiRXJyb3IiKQogICAgICAgIHJldHVybiAxCgogICAgdG9rZW4gPSBOb25lCiAgICBzZXJ2ZXIgPSBOb25lCiAgICBmb3IgaSwgYXJnIGluIGVudW1lcmF0ZShzeXMuYXJndik6CiAgICAgICAgaWYgYXJnID09ICctLXRva2VuJyBhbmQgaSArIDEgPCBsZW4oc3lzLmFyZ3YpOiB0b2tlbiA9IHN5cy5hcmd2W2kgKyAxXQogICAgICAgIGVsaWYgYXJnID09ICctLXNlcnZlcicgYW5kIGkgKyAxIDwgbGVuKHN5cy5hcmd2KTogc2VydmVyID0gc3lzLmFyZ3ZbaSArIDFdCgogICAgaWYgbm90IHRva2VuIG9yIG5vdCBzZXJ2ZXI6CiAgICAgICAgbXNnYm94KCJNaXNzaW5nIC0tdG9rZW4gb3IgLS1zZXJ2ZXIgYXJndW1lbnRzIiwgIkVycm9yIikKICAgICAgICByZXR1cm4gMQoKICAgIHRyeToKICAgICAgICByZXEgPSB1cmxsaWIucmVxdWVzdC5SZXF1ZXN0KGYne3NlcnZlcn0vYXBpL2NsYWltLWxhdW5jaC97dG9rZW59JykKICAgICAgICB3aXRoIHVybGxpYi5yZXF1ZXN0LnVybG9wZW4ocmVxLCB0aW1lb3V0PTE1KSBhcyByZXNwOgogICAgICAgICAgICBkYXRhID0ganNvbi5sb2FkcyhyZXNwLnJlYWQoKS5kZWNvZGUoKSkKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICBtc2dib3goZiJGYWlsZWQgdG8gY29ubmVjdCB0byBzZXJ2ZXI6XG57ZX1cblxuQ2hlY2sgeW91ciBpbnRlcm5ldCBjb25uZWN0aW9uLiIsICJDb25uZWN0aW9uIEVycm9yIikKICAgICAgICByZXR1cm4gMQoKICAgIGlmIG5vdCBkYXRhLmdldCgnb2snKToKICAgICAgICBtc2dib3goZGF0YS5nZXQoJ2Vycm9yJywgJ0xhdW5jaCBmYWlsZWQnKSwgIkVycm9yIikKICAgICAgICByZXR1cm4gMQoKICAgIHRvb2xfbmFtZSA9IGRhdGEuZ2V0KCd0b29sX25hbWUnLCAnVG9vbCcpCiAgICB1cmwgPSBkYXRhWyd1cmwnXQogICAgY29va2llc19yYXcgPSBqc29uLmxvYWRzKGRhdGFbJ2Nvb2tpZXMnXSkKICAgIHVzZXJuYW1lID0gZGF0YVsndXNlcm5hbWUnXQoKICAgIHRyeToKICAgICAgICBpbXBvcnQgcGxheXdyaWdodAogICAgZXhjZXB0IEltcG9ydEVycm9yOgogICAgICAgIHN1YnByb2Nlc3MucnVuKFtzeXMuZXhlY3V0YWJsZSwgJy1tJywgJ3BpcCcsICdpbnN0YWxsJywgJy0tdXBncmFkZScsICdwbGF5d3JpZ2h0J10sCiAgICAgICAgICAgICAgICAgICAgICAgY2FwdHVyZV9vdXRwdXQ9VHJ1ZSwgdGltZW91dD0xMjApCiAgICAgICAgaW1wb3J0bGliLmludmFsaWRhdGVfY2FjaGVzKCkKCiAgICB0cnk6CiAgICAgICAgZnJvbSBwbGF5d3JpZ2h0LnN5bmNfYXBpIGltcG9ydCBzeW5jX3BsYXl3cmlnaHQKICAgICAgICB3aXRoIHN5bmNfcGxheXdyaWdodCgpIGFzIHA6CiAgICAgICAgICAgIGV4ZSA9IHAuY2hyb21pdW0uZXhlY3V0YWJsZV9wYXRoCiAgICAgICAgICAgIGlmIG5vdCBleGUgb3Igbm90IG9zLnBhdGguaXNmaWxlKGV4ZSk6CiAgICAgICAgICAgICAgICBzdWJwcm9jZXNzLnJ1bihbc3lzLmV4ZWN1dGFibGUsICctbScsICdwbGF5d3JpZ2h0JywgJ2luc3RhbGwnLCAnY2hyb21pdW0nXSwKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRpbWVvdXQ9MzAwKQogICAgZXhjZXB0OgogICAgICAgIHN1YnByb2Nlc3MucnVuKFtzeXMuZXhlY3V0YWJsZSwgJy1tJywgJ3BsYXl3cmlnaHQnLCAnaW5zdGFsbCcsICdjaHJvbWl1bSddLAogICAgICAgICAgICAgICAgICAgICAgIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRpbWVvdXQ9MzAwKQoKICAgIGRlZiByb290X2RvbWFpbih1cmwpOgogICAgICAgIGZyb20gdXJsbGliLnBhcnNlIGltcG9ydCB1cmxwYXJzZQogICAgICAgIGhvc3QgPSB1cmxwYXJzZSh1cmwpLm5ldGxvYy5zcGxpdCgnQCcpWy0xXS5zcGxpdCgnOicpWzBdCiAgICAgICAgcGFydHMgPSBob3N0LnNwbGl0KCcuJykKICAgICAgICByZXR1cm4gJy4nLmpvaW4ocGFydHNbMTpdKSBpZiBsZW4ocGFydHMpID49IDMgZWxzZSBob3N0CgogICAgcm9vdCA9IHJvb3RfZG9tYWluKHVybCkKICAgIGNvb2tpZXMgPSBbXQogICAgZm9yIGMgaW4gY29va2llc19yYXc6CiAgICAgICAgaWYgbm90IGMuZ2V0KCduYW1lJyk6IGNvbnRpbnVlCiAgICAgICAgZG9tYWluID0gYy5nZXQoJ2RvbWFpbicsICcnKSBvciAnJwogICAgICAgIGlmIG5vdCBkb21haW4gb3IgZG9tYWluIGluICgnbnVsbCcsICd1bmRlZmluZWQnKToKICAgICAgICAgICAgZG9tYWluID0gJy4nICsgcm9vdCBpZiByb290IGVsc2UgJycKICAgICAgICBpZiBub3QgZG9tYWluOiBjb250aW51ZQogICAgICAgIGlmIG5vdCBkb21haW4uc3RhcnRzd2l0aCgnLicpIGFuZCBub3QgYy5nZXQoJ2hvc3RPbmx5JywgRmFsc2UpOgogICAgICAgICAgICBkb21haW4gPSAnLicgKyBkb21haW4KICAgICAgICBjb29raWUgPSB7J25hbWUnOiBjWyduYW1lJ10sICd2YWx1ZSc6IHN0cihjLmdldCgndmFsdWUnLCAnJykpLAogICAgICAgICAgICAgICAgICAnZG9tYWluJzogZG9tYWluLCAncGF0aCc6IGMuZ2V0KCdwYXRoJywgJy8nKSBvciAnLycsCiAgICAgICAgICAgICAgICAgICdzZWN1cmUnOiBib29sKGMuZ2V0KCdzZWN1cmUnLCBGYWxzZSkpLAogICAgICAgICAgICAgICAgICAnaHR0cE9ubHknOiBib29sKGMuZ2V0KCdodHRwT25seScsIEZhbHNlKSl9CiAgICAgICAgc3MgPSAoYy5nZXQoJ3NhbWVTaXRlJykgb3IgJycpLmxvd2VyKCkKICAgICAgICBpZiBzcyBpbiAoJ25vX3Jlc3RyaWN0aW9uJywgJ25vbmUnKToKICAgICAgICAgICAgY29va2llWydzYW1lU2l0ZSddID0gJ05vbmUnOyBjb29raWVbJ3NlY3VyZSddID0gVHJ1ZQogICAgICAgIGVsaWYgc3MgPT0gJ3N0cmljdCc6IGNvb2tpZVsnc2FtZVNpdGUnXSA9ICdTdHJpY3QnCiAgICAgICAgZWxpZiBzcyA9PSAnbGF4JzogY29va2llWydzYW1lU2l0ZSddID0gJ0xheCcKICAgICAgICBleHAgPSBjLmdldCgnZXhwaXJhdGlvbkRhdGUnKQogICAgICAgIGlmIGV4cCBhbmQgbm90IGMuZ2V0KCdzZXNzaW9uJyk6CiAgICAgICAgICAgIGNvb2tpZVsnZXhwaXJlcyddID0gaW50KGZsb2F0KGV4cCkpCiAgICAgICAgY29va2llcy5hcHBlbmQoY29va2llKQoKICAgIHVzZXJfZGF0YV9kaXIgPSB0ZW1wZmlsZS5ta2R0ZW1wKHByZWZpeD0nc3RfJykKICAgIHRyeToKICAgICAgICBmcm9tIHBsYXl3cmlnaHQuc3luY19hcGkgaW1wb3J0IHN5bmNfcGxheXdyaWdodAogICAgICAgIHdpdGggc3luY19wbGF5d3JpZ2h0KCkgYXMgcDoKICAgICAgICAgICAgaXNfc2VtcnVzaCA9ICdzZW1ydXNoLmNvbScgaW4gdXJsCiAgICAgICAgICAgIHRvb2xfYXJncyA9IFsnLS1zdGFydC1tYXhpbWl6ZWQnLCAnLS1kaXNhYmxlLWJsaW5rLWZlYXR1cmVzPUF1dG9tYXRpb25Db250cm9sbGVkJywgJy0tbm8tc2FuZGJveCddCiAgICAgICAgICAgIGNvbnRleHQgPSBwLmNocm9taXVtLmxhdW5jaF9wZXJzaXN0ZW50X2NvbnRleHQoCiAgICAgICAgICAgICAgICB1c2VyX2RhdGFfZGlyLCBoZWFkbGVzcz1GYWxzZSwKICAgICAgICAgICAgICAgIGFyZ3M9dG9vbF9hcmdzLAogICAgICAgICAgICAgICAgaWdub3JlX2RlZmF1bHRfYXJncz1bJy0tZW5hYmxlLWF1dG9tYXRpb24nXSwKICAgICAgICAgICAgICAgIG5vX3ZpZXdwb3J0PVRydWUsCiAgICAgICAgICAgICkKICAgICAgICAgICAgcGFnZSA9IGNvbnRleHQubmV3X3BhZ2UoKQogICAgICAgICAgICBwYWdlLmFkZF9pbml0X3NjcmlwdChBTlRJX1RIRUZUX0pTKQogICAgICAgICAgICBpZiBpc19zZW1ydXNoOgogICAgICAgICAgICAgICAgcGFnZS5hZGRfaW5pdF9zY3JpcHQoU0VNUlVTSF9KUykKCiAgICAgICAgICAgIHBhZ2UuZ290byh1cmwsIHdhaXRfdW50aWw9J2RvbWNvbnRlbnRsb2FkZWQnLCB0aW1lb3V0PTYwMDAwKQogICAgICAgICAgICBjb250ZXh0LmFkZF9jb29raWVzKGNvb2tpZXMpCiAgICAgICAgICAgIHBhZ2UucmVsb2FkKHdhaXRfdW50aWw9J2RvbWNvbnRlbnRsb2FkZWQnLCB0aW1lb3V0PTYwMDAwKQogICAgICAgICAgICBwYWdlLndhaXRfZm9yX2V2ZW50KCdjbG9zZScsIHRpbWVvdXQ9MCkKICAgICAgICAgICAgY29udGV4dC5jbG9zZSgpCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgbXNnYm94KGYiRXJyb3IgbGF1bmNoaW5nIGJyb3dzZXI6XG57ZX0iLCAiRXJyb3IiKQogICAgICAgIHJldHVybiAxCiAgICBmaW5hbGx5OgogICAgICAgIHRyeTogc2h1dGlsLnJtdHJlZSh1c2VyX2RhdGFfZGlyLCBpZ25vcmVfZXJyb3JzPVRydWUpCiAgICAgICAgZXhjZXB0OiBwYXNzCgogICAgcmV0dXJuIDAKCmlmIF9fbmFtZV9fID09ICdfX21haW5fXyc6CiAgICBzeXMuZXhpdChtYWluKCkpCg==";

    $bat_content = "@echo off\r\n";
    $bat_content .= "title Saqib Tools - $tool_name\r\n";
    $bat_content .= "cd /d \"%~dp0\"\r\n";
    $bat_content .= "REM Extract Python launcher script\r\n";
    $bat_content .= "if not exist \"launcher.py\" (\r\n";
    $bat_content .= "    echo Extracting launcher script...\r\n";
    $bat_content .= "    powershell -Command \"&{\$b='$b64';\$d=[Convert]::FromBase64String(\$b);[IO.File]::WriteAllBytes('launcher.py',\$d)}\" >nul 2>&1\r\n";
    $bat_content .= "    if not exist \"launcher.py\" (\r\n";
    $bat_content .= "        echo Failed to create launcher.py\r\n";
    $bat_content .= "        pause\r\n";
    $bat_content .= "        exit /b 1\r\n";
    $bat_content .= "    )\r\n";
    $bat_content .= ")\r\n";
    $bat_content .= "REM 1) Portable bundled Python\r\n";
    $bat_content .= "if exist \"python\\pythonw.exe\" (\r\n";
    $bat_content .= "    start \"\" \"python\\pythonw.exe\" \"launcher.py\" --token $token --server $base\r\n";
    $bat_content .= "    exit /b 0\r\n";
    $bat_content .= ")\r\n";
    $bat_content .= "REM 2) System Python\r\n";
    $bat_content .= "where python >nul 2>&1\r\n";
    $bat_content .= "if %ERRORLEVEL% == 0 (\r\n";
    $bat_content .= "    start \"\" pythonw \"launcher.py\" --token $token --server $base\r\n";
    $bat_content .= "    exit /b 0\r\n";
    $bat_content .= ")\r\n";
    $bat_content .= "echo Python not found. Install Python 3.9+ and try again.\r\n";
    $bat_content .= "pause\r\n";

    header('Content-Type: application/octet-stream');
    header('Content-Disposition: attachment; filename="' . $safe_name . '.bat"');
    header('Content-Length: ' . strlen($bat_content));
    header('Cache-Control: no-cache, no-store, must-revalidate');
    echo $bat_content;
    exit();

} catch (Exception $e) {
    http_response_code(500);
    echo 'Server error: ' . htmlspecialchars($e->getMessage());
    exit();
}
