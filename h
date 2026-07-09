[0;1;32m●[0m gunicorn-simple.service - gunicorn for QR Simple
     Loaded: loaded (]8;;file://nvjnbaxqpe/etc/systemd/system/gunicorn-simple.service/etc/systemd/system/gunicorn-simple.service]8;;; [0;1;32menabled[0m; preset: [0;1;32menabled[0m)
     Active: [0;1;32mactive (running)[0m since Thu 2026-07-09 16:11:25 MSK; 33min ago
   Main PID: 589943 (gunicorn)
      Tasks: 5 (limit: 4653)
     Memory: 162.7M (peak: 165.8M)
        CPU: 2min 51.132s
     CGroup: /system.slice/gunicorn-simple.service
             ├─[0;38;5;245m589943 /opt/qr_simple/venv/bin/python3 /opt/qr_simple/venv/bin/gunicorn --workers 3 --bind unix:/opt/qr_simple/gunicorn.sock qr_simple.wsgi:application[0m
             ├─[0;38;5;245m589947 /opt/qr_simple/venv/bin/python3 /opt/qr_simple/venv/bin/gunicorn --workers 3 --bind unix:/opt/qr_simple/gunicorn.sock qr_simple.wsgi:application[0m
             ├─[0;38;5;245m589948 /opt/qr_simple/venv/bin/python3 /opt/qr_simple/venv/bin/gunicorn --workers 3 --bind unix:/opt/qr_simple/gunicorn.sock qr_simple.wsgi:application[0m
             └─[0;38;5;245m589949 /opt/qr_simple/venv/bin/python3 /opt/qr_simple/venv/bin/gunicorn --workers 3 --bind unix:/opt/qr_simple/gunicorn.sock qr_simple.wsgi:application[0m

Jul 09 16:25:28 nvjnbaxqpe gunicorn[589948]: Not Found: /js/bootstrap.bundle.min.js
Jul 09 16:25:28 nvjnbaxqpe gunicorn[589948]: Not Found: /js/bootstrap.bundle.js
Jul 09 16:25:28 nvjnbaxqpe gunicorn[589947]: Not Found: /bootstrap/js/bootstrap.min.js
Jul 09 16:25:28 nvjnbaxqpe gunicorn[589949]: Not Found: /bootstrap/js/bootstrap.js
Jul 09 16:25:28 nvjnbaxqpe gunicorn[589949]: Not Found: /css/bootstrap.min.css
Jul 09 16:25:28 nvjnbaxqpe gunicorn[589948]: Not Found: /css/bootstrap.css
Jul 09 16:25:28 nvjnbaxqpe gunicorn[589949]: Not Found: /bootstrap/css/bootstrap.min.css
Jul 09 16:25:28 nvjnbaxqpe gunicorn[589949]: Not Found: /bootstrap/css/bootstrap.css
Jul 09 16:31:40 nvjnbaxqpe gunicorn[589948]: Not Found: /SDK/webLanguage
Jul 09 16:40:43 nvjnbaxqpe gunicorn[589948]: Not Found: /favicon.ico
