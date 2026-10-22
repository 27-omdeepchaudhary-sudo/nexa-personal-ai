"""Safe calculator for common spoken arithmetic, including Indian number terms.

Supported examples:
- 20 percent of 100000
- 20 percent of 1 lakh
- 20 percent of 1 lakh and 5000  -> two separate percentage calculations
- 1 lakh 50 thousand
- 2 lakh 25 thousand
- 5 crore
- 25 plus 15, 12 times 8, average of 10, 20, 30

This module intentionally avoids eval(); arithmetic expressions are parsed safely.
"""
import ast
import math
import operator
import re

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# Number terms commonly used in Indian English speech recognition.
_SMALL_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_MAGNITUDES = {
    "hundred": 100,
    "thousand": 1_000, "thousands": 1_000,
    "lakh": 100_000, "lakhs": 100_000,
    "lac": 100_000, "lacs": 100_000,
    "crore": 10_000_000, "crores": 10_000_000,
}


def _safe_eval(expr):
    tree = ast.parse(expr, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 8:
                raise ValueError("That exponent is too large for a voice calculation.")
            result = _BINOPS[type(node.op)](left, right)
            if isinstance(result, complex) or not math.isfinite(float(result)):
                raise ValueError("The result is outside the supported range.")
            return result
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](visit(node.operand))
        raise ValueError("Unsupported expression")

    return visit(tree)


def _fmt(value):
    if isinstance(value, float):
        if abs(value - round(value)) < 1e-10:
            return f"{round(value):,}"
        return f"{value:,.8f}".rstrip("0").rstrip(".")
    return f"{value:,}"


def _word_number_value(words):
    """Convert a compact number-word sequence to an integer, or return None."""
    words = [w for w in words if w not in {"and"}]
    if not words:
        return None

    # Digit-based sequences mixed with magnitude words: "1 lakh 50 thousand".
    total = 0
    current = 0
    saw_number = False
    for word in words:
        if word.isdigit():
            current += int(word)
            saw_number = True
        elif word in _SMALL_WORDS:
            current += _SMALL_WORDS[word]
            saw_number = True
        elif word in _MAGNITUDES:
            scale = _MAGNITUDES[word]
            saw_number = True
            if scale == 100:
                current = max(1, current) * 100
            else:
                total += max(1, current) * scale
                current = 0
        else:
            return None
    if not saw_number:
        return None
    return total + current


def _normalize_number_words(text):
    """Normalize Indian magnitudes without treating a bare 'and' as arithmetic."""
    t = text.lower().replace(",", "")
    # Match sequences like "one lakh fifty thousand", "1 lakh 50 thousand",
    # "five crore", or "one hundred twenty five".
    token = r"(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fourty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|thousands|lakh|lakhs|lac|lacs|crore|crores|and)"
    pattern = re.compile(rf"(?<![\w.])({token}(?:\s+{token}){{0,7}})(?![\w.])", re.I)

    def replace(match):
        phrase = match.group(1).strip()
        words = phrase.split()
        # Do not rewrite isolated operation words or a bare "and".
        if not any(w in _MAGNITUDES for w in words):
            return phrase
        value = _word_number_value(words)
        return str(value) if value is not None else phrase

    return pattern.sub(replace, t)


def _parse_simple_number(text):
    """Parse a single numeric value written as digits or a simple number word."""
    t = text.lower().strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        return float(t)
    if t in _SMALL_WORDS:
        return float(_SMALL_WORDS[t])
    return None


def calculate_spoken(text):
    """Return a spoken result if text clearly asks for arithmetic; otherwise None."""
    original = str(text).lower().strip()
    t = re.sub(r"^(?:nexa[, ]*)?(?:please[, ]*)?", "", original)
    t = re.sub(r"^(?:what is|what's|calculate|compute|work out|solve|tell me)\s+", "", t)
    t = t.rstrip("?.! ")

    # Explicitly support two separate bases in a request such as
    # "20 percent of 1 lakh and 5000".
    pct_token = r"(?:-?\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fourty|fifty|sixty|seventy|eighty|ninety)"
    separate = re.fullmatch(
        rf"\s*({pct_token})\s*(?:%|percent|per cent)\s+of\s+"
        r"(.+?)\s+and\s+(.+?)\s*",
        t,
    )
    if separate:
        pct_value = _parse_simple_number(separate.group(1))
        pct = pct_value if pct_value is not None else None
        left_raw, right_raw = separate.group(2).strip(), separate.group(3).strip()
        left = _parse_amount(left_raw)
        right = _parse_amount(right_raw)
        if pct is not None and left is not None and right is not None:
            return (
                f"{_fmt(pct)} percent of {_fmt(left)} is {_fmt(left * pct / 100)}. "
                f"{_fmt(pct)} percent of {_fmt(right)} is {_fmt(right * pct / 100)}."
            )

    # Percentage-of: normalize each amount independently, including lakh/crore.
    m = re.search(
        rf"({pct_token})\s*(?:%|percent|per cent)\s*(?:of|from)\s*(.+)$",
        t,
    )
    if m:
        pct_value = _parse_simple_number(m.group(1))
        pct = pct_value if pct_value is not None else None
        amount_text = m.group(2).strip()
        amount = _parse_amount(amount_text)
        if pct is not None and amount is not None:
            return f"{_fmt(pct)} percent of {_fmt(amount)} is {_fmt(amount * pct / 100)}."

    normalized = _normalize_number_words(t)

    # Average / mean of a list.
    m = re.search(r"(?:average|mean)\s+(?:of\s+)?(.+)$", normalized)
    if m:
        nums = re.findall(r"-?\d+(?:\.\d+)?", m.group(1))
        if len(nums) >= 2:
            vals = [float(n) for n in nums]
            return f"The average is {_fmt(sum(vals) / len(vals))}."

    # Common natural-language operations.
    ops = [
        (r"(-?\d+(?:\.\d+)?)\s+(?:plus|add)\s+(-?\d+(?:\.\d+)?)", "+", "plus"),
        (r"add\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)", "+", "plus"),
        (r"(-?\d+(?:\.\d+)?)\s+(?:minus|subtract)\s+(-?\d+(?:\.\d+)?)", "-", "minus"),
        (r"subtract\s+(-?\d+(?:\.\d+)?)\s+from\s+(-?\d+(?:\.\d+)?)", "rev-", "minus"),
        (r"(-?\d+(?:\.\d+)?)\s+(?:times|multiplied by|multiply by|into|x)\s+(-?\d+(?:\.\d+)?)", "*", "times"),
        (r"multiply\s+(-?\d+(?:\.\d+)?)\s+by\s+(-?\d+(?:\.\d+)?)", "*", "times"),
        (r"(-?\d+(?:\.\d+)?)\s+(?:divided by|divide by|over)\s+(-?\d+(?:\.\d+)?)", "/", "divided by"),
        (r"divide\s+(-?\d+(?:\.\d+)?)\s+by\s+(-?\d+(?:\.\d+)?)", "/", "divided by"),
    ]
    for pattern, op, label in ops:
        match = re.search(pattern, normalized)
        if match:
            a, b = map(float, match.groups())
            if op == "rev-":
                a, b, op = b, a, "-"
            try:
                result = _safe_eval(f"{a}{op}{b}")
            except ZeroDivisionError:
                return "You cannot divide by zero."
            except Exception:
                return "I couldn't safely calculate those numbers. Please say the operation again."
            return f"{_fmt(a)} {label} {_fmt(b)} equals {_fmt(result)}."

    # Explicit numeric expressions such as 12 * 8 or (45 + 5) / 2.
    if re.search(r"\d\s*[+*/%^-]\s*-?\d", normalized):
        expr = re.sub(r"[^0-9.()+*/%\-\s]", "", normalized)
        if expr.strip():
            try:
                return f"The answer is {_fmt(_safe_eval(expr))}."
            except ZeroDivisionError:
                return "You cannot divide by zero."
            except Exception:
                return "I couldn't safely calculate that expression. Please rephrase it."
    return None


def _parse_amount(text):
    """Parse one amount phrase such as '1 lakh', '1 lakh 50 thousand', or 'five crore'."""
    t = text.lower().strip().replace(",", "")
    t = re.sub(r"^(?:rupees?|rs\.?|inr)\s*", "", t)
    t = re.sub(r"\s*(?:rupees?|rs\.?|inr)$", "", t)
    t = _normalize_number_words(t)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        try:
            return float(t)
        except ValueError:
            return None
    # Permit numeric amount with currency words already stripped.
    return None
