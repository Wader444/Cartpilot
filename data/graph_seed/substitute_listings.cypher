// CartPilot — Substitute listings add-on (Member B) — prepared 2026-11-10
// Run AFTER cartpilot-neo4j-seed.cypher, in Neo4j Browser or cypher-shell.
// It does NOT touch the verified seed file: the seed stays as-is (30 listings).
//
// What it does:
//   1. Flips P11, P12, P13 from listed:false to listed:true.
//   2. Adds 9 listings (3 products x 3 platforms) + HAS_LISTING edges,
//      in the same one-statement-per-listing pattern as the fixed seed.
//   3. Adds 9 SAME_AS edges (3 per product), same pattern as the seed.
//
// Why: the SUBSTITUTE_FOR targets in the seed (P03->P11 0.78, P05->P12 0.75,
// P02->P13 0.72) are graph-only, so get_substitute() in
// cartpilot-match-products.py returns buyable=False for all three. After this
// file runs, those same calls return buyable=True, and the Hour 14
// fault-injection test ("an out-of-stock item yields a valid substitute")
// can pass as written, with the substitute actually filling a cart.
//
// MOCK PRICES — illustrative only, set by analogy to the starter catalog,
// not scraped or real prices. Masoor dal is priced below Toor Dal
// (Blinkit 178 / Zepto 182 / Instamart 172); Groundnut Oil above Sunflower
// Oil (168 / 175 / 162); Sona Masoori Rice well below Basmati Rice
// (145 / 139 / 152). ETAs match the existing per-platform values
// (Blinkit 12, Zepto 10, Instamart 25 mins).
//
// Companion file: cartpilot-substitute-listings.csv — the same 9 rows in
// catalog-CSV form. Append them to cartpilot-starter-catalog.csv if you
// want the PlatformAdapter / match_products to see the substitutes too.
//
// WARNING — demo check: cartpilot-match-products.py hard-codes
// "10 groups x 3 listings" (len(groups) == 10). Once the adapter serves
// 13 products, that check must become 13 groups, or it will FAIL even
// though matching is correct. Change only that number (line ~395) and the
// "10 groups" label; do not re-tune the matcher.
//
// Re-running is safe: MERGE + the listing_id constraint make it idempotent.

// --- Flip the three substitutes to buyable ---
MATCH (p:Product {sku: 'P11'}) SET p.listed = true;
MATCH (p:Product {sku: 'P12'}) SET p.listed = true;
MATCH (p:Product {sku: 'P13'}) SET p.listed = true;

// --- Listings (9) + HAS_LISTING edges: ONE statement per listing ---
// P11 — Tata Sampann Masoor Dal 1kg (substitute for P03 Toor Dal, 0.78)
MERGE (l:Listing {product_id: 'BLK-MASOOR-1KG', platform: 'blinkit', name: 'Tata Sampann Masoor Dal 1kg', price: 158, mrp: 175, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P11'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-MASOOR-1KG', platform: 'zepto', name: 'Tata Sampann Masoor Dal 1 kg', price: 162, mrp: 175, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P11'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-MASOOR-1KG', platform: 'instamart', name: 'Masoor Dal 1kg - Tata Sampann', price: 154, mrp: 175, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P11'})
MERGE (p)-[:HAS_LISTING]->(l);
// P12 — Fortune Groundnut Oil 1L (substitute for P05 Sunflower Oil, 0.75)
MERGE (l:Listing {product_id: 'BLK-GROUNDNUT-OIL-1L', platform: 'blinkit', name: 'Fortune Groundnut Oil 1L', price: 188, mrp: 205, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P12'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-GROUNDNUT-OIL-1L', platform: 'zepto', name: 'Fortune Groundnut Oil 1 L', price: 195, mrp: 205, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P12'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-GROUNDNUT-OIL-1L', platform: 'instamart', name: 'Groundnut Oil 1L - Fortune', price: 182, mrp: 205, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P12'})
MERGE (p)-[:HAS_LISTING]->(l);
// P13 — India Gate Sona Masoori Rice 1kg (substitute for P02 Basmati Rice, 0.72)
MERGE (l:Listing {product_id: 'BLK-SONA-1KG', platform: 'blinkit', name: 'India Gate Sona Masoori Rice 1kg', price: 72, mrp: 80, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P13'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-SONA-1KG', platform: 'zepto', name: 'India Gate Sona Masoori Rice 1 kg', price: 68, mrp: 80, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P13'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-SONA-1KG', platform: 'instamart', name: 'Sona Masoori Rice 1kg - India Gate', price: 75, mrp: 80, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P13'})
MERGE (p)-[:HAS_LISTING]->(l);

// --- SAME_AS between the new listings (3 per product = 9) ---
MATCH (a:Listing {product_id: 'BLK-MASOOR-1KG'}), (b:Listing {product_id: 'INS-MASOOR-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-MASOOR-1KG'}), (b:Listing {product_id: 'ZEP-MASOOR-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-MASOOR-1KG'}), (b:Listing {product_id: 'ZEP-MASOOR-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-GROUNDNUT-OIL-1L'}), (b:Listing {product_id: 'INS-GROUNDNUT-OIL-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-GROUNDNUT-OIL-1L'}), (b:Listing {product_id: 'ZEP-GROUNDNUT-OIL-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-GROUNDNUT-OIL-1L'}), (b:Listing {product_id: 'ZEP-GROUNDNUT-OIL-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-SONA-1KG'}), (b:Listing {product_id: 'INS-SONA-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-SONA-1KG'}), (b:Listing {product_id: 'ZEP-SONA-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-SONA-1KG'}), (b:Listing {product_id: 'ZEP-SONA-1KG'}) MERGE (a)-[:SAME_AS]->(b);

// --- Test queries (run after seeding) ---
// MATCH (p:Product) RETURN count(p) AS products;              // expect 15 (13 listed + P14/P15 graph-only)
// MATCH (l:Listing) RETURN count(l) AS listings;              // expect 39 (30 + 9)
// MATCH ()-[r:SAME_AS]->() RETURN count(r) AS same_as;        // expect 39 (30 + 9)
// MATCH (p:Product {sku: 'P03'})-[s:SUBSTITUTE_FOR]->(sub:Product) RETURN sub.name, s.confidence, sub.listed;  // expect P11, 0.78, true
// MATCH (p:Product {sku: 'P11'})-[:HAS_LISTING]->(l) RETURN l.platform, l.name, l.price;
