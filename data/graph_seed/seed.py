"""CartPilot - run the Neo4j seed file without copy-pasting.

Use this when Neo4j Desktop's Query editor will not take a pasted
multi-statement script (that editor runs one query per card).

Usage (in VS Code terminal, from the project folder):
    pip install neo4j
    python data/graph_seed/seed.py
    # or: python data/graph_seed/seed.py data/graph_seed/seed.cypher

Connection comes from the environment, falling back to local Desktop:
    NEO4J_URI      default bolt://localhost:7687
    NEO4J_USER     default neo4j
    NEO4J_PASSWORD if unset, you will be asked (typing is hidden)

The seed file itself is idempotent (MERGE + constraints), so running
this twice will not duplicate nodes.
"""

import getpass
import os
import sys
from pathlib import Path

# Load cartpilot/.env if python-dotenv is installed (pip install python-dotenv).
# Without it, NEO4J_PASSWORD must be set in the terminal, or you will be asked.
# The walk-up below matters: running from a subfolder must still find
# the .env that lives at the project root.
try:
    from dotenv import load_dotenv

    load_dotenv()
    for _parent in Path(__file__).resolve().parents:
        _env_file = _parent / ".env"
        if _env_file.exists():
            load_dotenv(_env_file, override=False)
            break
except ImportError:
    pass

from neo4j import GraphDatabase


def load_statements(cypher_path: Path) -> list[str]:
    """Strip full-line // comments, then split on ';'."""
    lines = []
    for line in cypher_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("//"):
            continue
        lines.append(line)
    text = "\n".join(lines)
    return [s.strip() for s in text.split(";") if s.strip()]


def main() -> int:
    cypher_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("seed.cypher")
    if not cypher_path.exists():
        print(f"Seed file not found: {cypher_path}")
        print("Put seed.cypher in the same folder as this script, or pass its path as an argument.")
        return 1

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD") or getpass.getpass("Neo4j password for user 'neo4j': ")

    statements = load_statements(cypher_path)
    print(f"Loaded {len(statements)} statements from {cypher_path.name}")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session(database="cartpilot") as session:
        for i, stmt in enumerate(statements, start=1):
            session.run(stmt)
            if i % 20 == 0 or i == len(statements):
                print(f"  ran {i}/{len(statements)}")
        # Verify - expect 15 products, 30 listings, 30 SAME_AS edges
        products = session.run("MATCH (p:Product) RETURN count(p) AS n").single()["n"]
        listings = session.run("MATCH (l:Listing) RETURN count(l) AS n").single()["n"]
        same_as = session.run("MATCH ()-[r:SAME_AS]->() RETURN count(r) AS n").single()["n"]
    driver.close()
    print(f"Done. Products: {products} (expect 15), Listings: {listings} (expect 30), SAME_AS: {same_as} (expect 30)")
    return 0 if (products, listings, same_as) == (15, 30, 30) else 2


if __name__ == "__main__":
    raise SystemExit(main())
