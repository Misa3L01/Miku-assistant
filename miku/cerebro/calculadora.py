# -*- coding: utf-8 -*-
"""
calculadora.py - Evaluador aritmético local (sin LLM ni API).

Fast-path para frases como "cuánto es 15 por 23 más 10%". Convierte el texto
en español a una expresión aritmética y la evalúa de forma SEGURA (sin usar
``eval``): se implementa un parser de descenso recursivo con soporte de:

    - ``+`` ``-`` ``*`` ``/`` y ``%`` (porcentaje).
    - Paréntesis simples.
    - Palabras en español: "más", "menos", "por", "entre"/"dividido".

Regla del porcentaje: ``X más 10%`` => X + 10% de X. Igual para "menos". Un
``%`` suelto entre dos números se interpreta como módulo.

Diseño defensivo: cualquier error devuelve ``None`` para que el llamador
decida qué responder (así no rompemos el flujo normal del parser).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Números: enteros o decimales (con coma o punto).
_RE_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")

# Reemplazos de palabras en español -> operadores (normalizado sin acentos).
_REEMPLAZOS: List[Tuple[str, str]] = [
    ("multiplicado por", "*"),
    ("dividido por", "/"),
    ("dividido entre", "/"),
    ("elevado a", "^"),
    ("mas", "+"),
    ("menos", "-"),
    ("por", "*"),
    ("entre", "/"),
    ("dividido", "/"),
    ("x", "*"),  # "5 x 3"
]


def _normalizar(texto: str) -> str:
    """Minúsculas + sin acentos, para reconocer palabras del español."""
    tabla = str.maketrans("áàäâãéèëêíìïîóòöôõúùüûñç",
                          "aaaaaeeeeiiiiooooouuuunc")
    return (texto or "").lower().translate(tabla)


def parece_calculo(texto: str) -> bool:
    """Heurística: ¿el texto parece una operación aritmética?

    Exige al menos un operador reconocible y al menos un número. Evita gastar
    la API en frases que no son cálculos.
    """
    t = _normalizar(texto)
    if not _RE_NUMERO.search(t):
        return False
    # Algún operador (símbolo o palabra).
    if re.search(r"[+\-*/%x]", t):
        return True
    return any(p in t for p in ("mas", "menos", "por", "entre", "dividido"))


def calcular(texto: str) -> Optional[str]:
    """Intenta resolver una operación aritmética escrita en lenguaje natural.

    Devuelve el resultado formateado (str) o None si no es un cálculo válido o
    hubo un error.
    """
    try:
        expr = _a_expresion(texto)
        if expr is None:
            return None
        tokens = _tokenizar(expr)
        if not tokens:
            return None
        parser = _Parser(tokens)
        valor = parser.parse()
        return _formatear(valor)
    except Exception:  # noqa: BLE001
        return None


# Palabras de relleno que se pueden ignorar sin cambiar el cálculo.
_RE_RELLENO = re.compile(r"\b(?:es|son|el|la|los|las|da|seria|cuenta)\b")
# Cualquier palabra (letras) que quede después de los reemplazos.
_PALABRAS_SUELTAS = re.compile(r"[a-z]")
# Número con separador de miles: 1.500 / 12.345.678 (grupos de 3 dígitos).
_RE_MILES = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+(?![\d.])")


def _quitar_puntos_de_miles(texto: str) -> str:
    """Quita el punto de miles ("1.500" -> "1500") para no leerlo como 1,5."""
    return _RE_MILES.sub(lambda m: m.group(0).replace(".", ""), texto)


def _a_expresion(texto: str) -> Optional[str]:
    """Convierte el texto natural en una expresión aritmética con símbolos.

    Saca prefijos conversacionales ("cuánto es", "calculá", "cuanto da") y
    reemplaza las palabras operadoras por símbolos. Devuelve None si no hay
    nada aprovechable.
    """
    t = _normalizar(texto)

    # Quitamos prefijos conversacionales típicos.
    for prefijo in ("cuanto es", "cuanto da", "cuanto seria", "cuanto son",
                    "cuanto", "calculame", "calcula", "resolveme", "resuelve",
                    "resultado de", "haceme"):
        if t.startswith(prefijo):
            t = t[len(prefijo):].strip()
            break

    # Miles con punto al estilo argentino ("1.500" -> 1500, "2.000.000" -> 2000000).
    t = _quitar_puntos_de_miles(t)

    # "el 10% de 340" -> "0.1 * 340" (caso especial útil).
    m = re.match(r"^(?:el\s+)?(\d+(?:[.,]\d+)?)\s*%\s*de\s+(.+)$", t)
    if m:
        pct = float(m.group(1).replace(",", ".")) / 100.0
        resto = m.group(2)
        # ``:f`` evita la notación científica ("1e-05") que el tokenizador rompe.
        return f"{pct:.10f} * ( {resto} )"

    # Reemplazos de palabras por operadores.
    for palabra, simbolo in _REEMPLAZOS:
        t = re.sub(rf"\b{re.escape(palabra)}\b", simbolo, t)

    # Normalizamos la coma decimal (1,5 -> 1.5) solo entre dígitos.
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)

    # Si quedan palabras que no entendemos ("la mitad de", "por ciento",
    # "dolares en pesos") o un operador no soportado ("^"), NO calculamos:
    # descartarlas en silencio daba respuestas falsas ("5 por ciento de 200"
    # -> "Son 1000"). El LLM se encarga de esas frases.
    if "^" in t or _PALABRAS_SUELTAS.search(_RE_RELLENO.sub(" ", t)):
        return None

    # Dejamos SOLO caracteres válidos para la expresión.
    t = re.sub(r"[^0-9+\-*/().% ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


# ---------------- Tokenizador ---------------- #
def _es_numero(token: str) -> bool:
    """True si el token es un número (entero o decimal)."""
    return bool(token) and _RE_NUMERO.fullmatch(token) is not None


def _tokenizar(expr: str) -> List[str]:
    """Divide la expresión en tokens (números y operadores).

    Además fusiona ``<número> %`` en un token ``PCT:<número>`` cuando el ``%``
    es POSTFijo (porcentaje): es decir, cuando luego del ``%`` NO viene un
    número. Si viene un número (``10 % 3``), el ``%`` se trata como MÓDULO.
    """
    bruto: List[str] = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or c == ".":
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            bruto.append(expr[i:j])
            i = j
            continue
        if c in "+-*/%()":
            bruto.append(c)
            i += 1
            continue
        # Cualquier otra cosa: la descartamos.
        i += 1

    # Post-paso: fusión de "número %" (porcentaje postfijo) cuando corresponde.
    tokens: List[str] = []
    for k, tok in enumerate(bruto):
        if (tok == "%" and tokens and _es_numero(tokens[-1])
                and (k + 1 >= len(bruto) or not _es_numero(bruto[k + 1]))):
            num = tokens.pop()
            tokens.append(f"PCT:{num}")
            continue
        tokens.append(tok)
    return tokens


# ---------------- Parser (descenso recursivo) ---------------- #
class _Parser:
    """Parser de expresiones con precedencia estándar.

    Gramática:
        expresion := termino (('+' | '-') termino)*
        termino   := factor (('*' | '/' | '%') factor)*
        factor    := numero | '(' expresion ')' | '-' factor
    Además maneja el sufijo de porcentaje para "X + 10%".
    """

    def __init__(self, tokens: List[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _ver(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _tomar(self) -> Optional[str]:
        tok = self._ver()
        if tok is not None:
            self.pos += 1
        return tok

    def parse(self) -> float:
        valor = self._expresion()
        if self._ver() is not None:
            raise ValueError("Tokens sobrantes")
        return valor

    def _expresion(self) -> float:
        valor = self._termino()
        while True:
            op = self._ver()
            if op in ("+", "-"):
                self._tomar()
                nxt = self._ver()
                # Porcentaje contextual: "X + 10%" => X + 10% de X.
                if nxt is not None and nxt.startswith("PCT:"):
                    self._tomar()
                    pct = float(nxt[4:])
                    valor = (valor + valor * pct / 100.0) if op == "+" else \
                        (valor - valor * pct / 100.0)
                else:
                    derecho = self._termino()
                    valor = valor + derecho if op == "+" else valor - derecho
            else:
                break
        return valor

    def _termino(self) -> float:
        valor = self._factor()
        while True:
            op = self._ver()
            if op in ("*", "/", "%"):
                self._tomar()
                derecho = self._factor()
                if op == "*":
                    valor = valor * derecho
                elif op == "/":
                    if derecho == 0:
                        raise ZeroDivisionError("División por cero")
                    valor = valor / derecho
                else:  # %
                    if derecho == 0:
                        raise ZeroDivisionError("Módulo por cero")
                    valor = valor % derecho
            else:
                break
        return valor

    def _factor(self) -> float:
        tok = self._ver()
        if tok is None:
            raise ValueError("Expresión incompleta")
        if tok == "-":
            self._tomar()
            return -self._factor()
        if tok == "+":
            self._tomar()
            return self._factor()
        if tok == "(":
            self._tomar()
            valor = self._expresion()
            if self._tomar() != ")":
                raise ValueError("Falta paréntesis de cierre")
            return valor
        # Token de porcentaje suelto (ej. "10%"): vale como su número.
        if tok.startswith("PCT:"):
            self._tomar()
            return float(tok[4:])
        if _RE_NUMERO.fullmatch(tok):
            self._tomar()
            return float(tok)
        # Token inesperado.
        raise ValueError(f"Token inesperado: {tok}")


def _formatear(valor: float) -> str:
    """Formatea el resultado: entero cuando es exacto, si no con 2 decimales."""
    if abs(valor - round(valor)) < 1e-9:
        return str(int(round(valor)))
    return f"{valor:.2f}".rstrip("0").rstrip(".")