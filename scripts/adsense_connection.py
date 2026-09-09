"""Connect only public editorial HTML to the owner's AdSense account.

Runs after the validated site is assembled. No data, credentials, release counts,
email configuration, or source content is changed. The Google loader is paused
until Google's real consent response permits ads. Utility pages are excluded.
"""
from pathlib import Path
import re

PUBLISHER = 'ca-pub-5170234738371400'
MARKER = '<!-- AIEO AdSense connection v1 -->'
BLOCK = '<!-- AIEO AdSense connection v1 -->\n<meta name="google-adsense-account" content="ca-pub-5170234738371400">\n<script>\n(function () {\n  \'use strict\';\n  if (window.__aieoPublisherConnection) return;\n  window.__aieoPublisherConnection = true;\n  window.adsbygoogle = window.adsbygoogle || [];\n  window.adsbygoogle.pauseAdRequests = 1;\n  window.googlefc = window.googlefc || {};\n  window.googlefc.callbackQueue = window.googlefc.callbackQueue || [];\n  var tcfKnown = false, tcfAllowed = false, modeKnown = false, modeAllowed = false;\n  var modeDenied = false, usDenied = false, running = false;\n  function apply() {\n    var allow = !modeDenied && !usDenied && (tcfKnown ? tcfAllowed : (modeKnown && modeAllowed));\n    window.adsbygoogle.pauseAdRequests = allow ? 0 : 1;\n    if (running && !allow) { running = false; window.location.reload(); }\n    else running = allow;\n  }\n  function readMode() {\n    try {\n      if (typeof window.googlefc.getGoogleConsentModeValues !== \'function\') return;\n      var values = window.googlefc.getGoogleConsentModeValues();\n      var keys = [\'adStoragePurposeConsentStatus\', \'adUserDataPurposeConsentStatus\', \'adPersonalizationPurposeConsentStatus\'];\n      modeKnown = true;\n      modeAllowed = keys.every(function (k) { return values && (values[k] === 1 || values[k] === 3); });\n      modeDenied = keys.some(function (k) { return values && values[k] === 2; });\n    } catch (_) { modeKnown = true; modeAllowed = false; modeDenied = true; }\n    apply();\n  }\n  window.googlefc.callbackQueue.push({CONSENT_MODE_DATA_READY: readMode});\n  window.googlefc.callbackQueue.push({INITIAL_US_STATES_OPT_OUT_DATA_READY: function () {\n    try {\n      var api = window.googlefc.usstatesoptout;\n      if (api && typeof api.getInitialUsStatesOptOutStatus === \'function\') {\n        var state = api.getInitialUsStatesOptOutStatus();\n        usDenied = state !== 1 && state !== 2;\n        apply();\n      }\n    } catch (_) { usDenied = true; apply(); }\n  }});\n  window.googlefc.callbackQueue.push({CONSENT_API_READY: function () {\n    if (typeof window.__tcfapi === \'function\') {\n      window.__tcfapi(\'addEventListener\', 2, function (data, ok) {\n        if (!ok || !data || data.cmpStatus === \'error\') {\n          tcfKnown = true; tcfAllowed = false; apply(); return;\n        }\n        if (data.eventStatus === \'cmpuishown\') {\n          window.adsbygoogle.pauseAdRequests = 1; return;\n        }\n        if ([\'tcloaded\', \'useractioncomplete\'].indexOf(data.eventStatus) < 0) return;\n        var purposes = data.purpose && data.purpose.consents;\n        var vendors = data.vendor && data.vendor.consents;\n        tcfKnown = typeof data.gdprApplies === \'boolean\';\n        // Conservative opt-in. Google still evaluates all remaining TCF/GPP restrictions.\n        tcfAllowed = data.gdprApplies === false || !!(data.gdprApplies === true &&\n          data.tcString && purposes && purposes[1] && purposes[3] && purposes[4] && vendors && vendors[755]);\n        apply();\n      });\n    }\n    function privacyLink() {\n      if (typeof window.googlefc.showRevocationMessage !== \'function\') return;\n      var footer = document.querySelector(\'footer\');\n      if (!footer || document.getElementById(\'aieo-ad-privacy\')) return;\n      var button = document.createElement(\'button\');\n      button.type = \'button\'; button.id = \'aieo-ad-privacy\';\n      button.textContent = \'Advertising privacy choices\';\n      button.addEventListener(\'click\', function () {\n        window.adsbygoogle.pauseAdRequests = 1;\n        window.googlefc.callbackQueue.push({CONSENT_API_READY: function () {\n          window.googlefc.showRevocationMessage();\n        }});\n      });\n      footer.appendChild(button);\n    }\n    if (document.readyState === \'loading\') document.addEventListener(\'DOMContentLoaded\', privacyLink, {once:true});\n    else privacyLink();\n  }});\n})();\n</script>\n<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-5170234738371400"\n     crossorigin="anonymous"></script>\n<!-- /AIEO AdSense connection v1 -->'


def connect_site(site: Path) -> int:
    count = 0
    for path in sorted(site.rglob('*.html')):
        if path.is_symlink():
            raise ValueError('Refusing an HTML symlink in the public site')
        rel = path.relative_to(site)
        # No ads on forms, status/owner screens, private material or privacy notices.
        if len(rel.parts) > 1 and rel.parts[0] not in {'edu', 'reports', 'methodology'}:
            continue
        if len(rel.parts) == 1 and rel.name != 'index.html':
            continue
        source = path.read_text(encoding='utf-8')
        if re.search(r'<meta\b[^>]*name=[\"\']robots[\"\'][^>]*noindex', source, re.I):
            continue
        if MARKER in source:
            if source.count(MARKER) != 1:
                raise ValueError('Duplicate publisher connection: ' + str(rel))
            continue
        if 'pagead2.googlesyndication.com/pagead/js/adsbygoogle.js' in source:
            raise ValueError('Existing ad loader needs review before adding another: ' + str(rel))
        if source.lower().count('</head>') != 1:
            raise ValueError('Expected one HTML head: ' + str(rel))
        source = re.sub(r'</head>', lambda m: '\n' + BLOCK + '\n' + m.group(), source, count=1, flags=re.I)
        path.write_text(source, encoding='utf-8')
        count += 1
    if not (site / 'index.html').is_file() or MARKER not in (site / 'index.html').read_text(encoding='utf-8'):
        raise ValueError('The Observatory homepage was not connected to AdSense')
    print('AdSense publisher connection installed on', count, 'public pages; consent remains required.')
    return count
