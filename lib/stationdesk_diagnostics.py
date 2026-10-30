"""Small, credential-safe failure excerpts shared by CLI and app store."""
import re


def diagnostic_tail(output):
    # Scrub the complete text before taking an excerpt, including multiline URLs.
    output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(output))
    output = re.sub(r'(https?://)[^\s/@]+(?::[^\s/@]*)?@', r'\1[redacted]@', output)
    output = re.sub(r'(?i)(\b(?:[\w-]*(?:token|password|secret|api[_-]?key)[\w-]*)[\s"\']*[:=][\s"\']*)[^\s"\'&,;]+',
                    r'\1[redacted]', output)
    output = re.sub(r'(?i)(\bAuthorization:\s*(?:Bearer|Basic)\s+)\S+', r'\1[redacted]', output)
    output = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b', '[redacted]', output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return '\n'.join(lines[-6:])[-2400:]
