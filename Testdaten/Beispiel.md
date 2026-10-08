# Beispieldokument

Diese Datei dient zum schnellen Ausprobieren des Markdown Viewers. Sie deckt die
Elemente ab, die der Viewer darstellt.

## Textauszeichnung

Ein Absatz mit **fettem**, *kursivem* und ~~durchgestrichenem~~ Text, etwas
`Inline-Code` und einem [Link](https://www.markdownguide.org/).

> Ein Zitat, das über
> zwei Zeilen läuft.

## Listen

- Erster Punkt
- Zweiter Punkt
  - Eingerückter Unterpunkt
  - Noch einer
- Dritter Punkt

1. Schritt eins
2. Schritt zwei
3. Schritt drei

## Tabelle

| Engine | Schlüssel nötig | Netzwerk | Stärke              |
|--------|-----------------|----------|---------------------|
| lokal  | nein            | nein     | läuft offline       |
| API    | ja              | ja       | Tabellen, Struktur  |

## Code

```python
def fakultaet(n: int) -> int:
    return 1 if n <= 1 else n * fakultaet(n - 1)

print(fakultaet(5))  # 120
```

## Formeln

Inline: $e^{i\pi} + 1 = 0$

Abgesetzt:

$$
\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}
$$

### Unterabschnitt

Überschriften der Ebenen 1 bis 3 erscheinen im Inhaltsverzeichnis.

---

Ende des Beispiels.
