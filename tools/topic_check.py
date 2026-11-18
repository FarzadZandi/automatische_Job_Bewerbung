"""Topic-model cross-check for the number of CV families (lexical: TF-IDF + NMF).

For every k in a range, NMF is fitted with several random seeds. Reported per k: reconstruction error and
stability (how similar the top terms of the topics stay across seeds; 1.0 = identical). Stable k values are
candidates for N; Claude's family decision still rests on the tests in guide/04. When vacancies carry
claude_family labels, a topic x family table shows where topics and families agree.

Usage: python tools/topic_check.py VACANCY_DIR OUT_MD [--kmin 3] [--kmax 15] [--seeds 5]
Exit code 2 when scikit-learn is not installed.
"""
from __future__ import annotations

import argparse
import itertools
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vacancy_io import STOP, load, normalize, term_text  # noqa: E402

TOP = 15


def _top_terms(model, names, top=TOP):
    return [[names[i] for i in row.argsort()[::-1][:top]] for row in model.components_]


def _stability(runs):
    scores = []
    for a, b in itertools.combinations(runs, 2):
        remaining = [set(t) for t in b]
        total = 0.0
        for topic in (set(t) for t in a):
            best = max(range(len(remaining)), key=lambda i: len(topic & remaining[i]) / len(topic | remaining[i]))
            total += len(topic & remaining[best]) / len(topic | remaining[best])
            remaining.pop(best)
        scores.append(total / len(a))
    return sum(scores) / len(scores) if scores else 1.0


def run(folder: Path, kmin: int, kmax: int, seeds: int) -> tuple[int, str]:
    try:
        from sklearn.decomposition import NMF
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return 2, "scikit-learn is not installed; the topic cross-check was skipped.\n"
    items = load(folder, "main")
    docs = [normalize(term_text(item)) for item in items]
    lines = ["# Topic check (TF-IDF + NMF)", "", f"Main vacancies: {len(docs)}", ""]
    if len(docs) < 6:
        return 0, "\n".join(lines + ["Too few vacancies for a topic model (at least 6 needed).", ""])
    vectorizer = TfidfVectorizer(stop_words=sorted(STOP), ngram_range=(1, 2), min_df=2 if len(docs) >= 20 else 1,
                                 max_df=0.9, token_pattern=r"(?u)\b[a-z][a-z0-9+#.-]{2,}\b", sublinear_tf=True)
    matrix = vectorizer.fit_transform(docs)
    names = vectorizer.get_feature_names_out()
    kmax = min(kmax, len(docs) - 1, matrix.shape[1] - 1)
    results = []
    for k in range(max(2, kmin), kmax + 1):
        runs, errors, models = [], [], []
        for seed in range(seeds):
            model = NMF(n_components=k, init="random", random_state=seed, max_iter=800)
            model.fit(matrix)
            runs.append(_top_terms(model, names)); errors.append(model.reconstruction_err_); models.append(model)
        results.append((k, sum(errors) / len(errors), _stability(runs), models[0]))
    lines += ["| k | Reconstruction error | Stability |", "|---:|---:|---:|"]
    lines += [f"| {k} | {e:.3f} | {s:.2f} |" for k, e, s, _ in results]
    best = sorted(results, key=lambda r: (-round(r[2], 2), r[0]))[:3]
    lines += ["", "Most stable k (candidates, not a decision): " + ", ".join(str(r[0]) for r in best), ""]
    labels = [item["family"] for item in items]
    for k, _, stability, model in best:
        lines += [f"## k = {k} (stability {stability:.2f})", ""]
        weights = model.transform(matrix)
        for index, terms in enumerate(_top_terms(model, names, 10)):
            lines.append(f"- Topic {index + 1}: " + ", ".join(terms))
        if any(labels):
            assigned = weights.argmax(axis=1)
            table = Counter((f"T{t + 1}", label or "none") for t, label in zip(assigned, labels))
            families = sorted({label or "none" for label in labels})
            lines += ["", "| Topic | " + " | ".join(families) + " |", "|---|" + "---:|" * len(families)]
            for t in range(k):
                lines.append(f"| T{t + 1} | " + " | ".join(str(table[(f'T{t + 1}', f)]) for f in families) + " |")
        lines.append("")
    lines += ["Read with care: topics are word patterns, families are positioning decisions. A family that splits "
              "over two stable topics is a split candidate; two families sharing one topic are merge candidates.", ""]
    return 0, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vacancies", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--kmin", type=int, default=3)
    parser.add_argument("--kmax", type=int, default=15)
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args(argv)
    code, report = run(args.vacancies, args.kmin, args.kmax, args.seeds)
    print(report if code == 2 else f"Wrote {args.out}")
    if code != 2:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
