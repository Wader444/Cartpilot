// CartPilot - Neo4j seed (Member B) - FIXED 2026-11-06
// Generated from cartpilot-starter-catalog.csv (10 products x 3 platforms = 30 listings).
// Run in Neo4j Browser (paste it in) or: cypher-shell -f cartpilot-neo4j-seed.cypher
// or run: python cartpilot-neo4j-seed.py
// Re-running is safe: MERGE + constraints make it idempotent. Clear first with MATCH (n) DETACH DELETE n only if you want a fresh DB.
// FIX: each Listing + its HAS_LISTING edge is now ONE statement. Previously the
// WITH l line was a separate statement, so 'l' did not exist there and the Browser
// failed at statement 19 and stopped. That was a bug in this file, not your setup.

// --- Constraints ---
CREATE CONSTRAINT product_sku IF NOT EXISTS FOR (p:Product) REQUIRE p.sku IS UNIQUE;
CREATE CONSTRAINT listing_id IF NOT EXISTS FOR (l:Listing) REQUIRE l.product_id IS UNIQUE;

// --- Canonical products (10, listed: true) ---
MERGE (p:Product {sku: 'P01', name: 'Aashirvaad Whole Wheat Atta', brand: 'Aashirvaad', quantity_value: 5, quantity_unit: 'kg', category: 'staples', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P02', name: 'India Gate Basmati Rice', brand: 'India Gate', quantity_value: 1, quantity_unit: 'kg', category: 'staples', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P03', name: 'Tata Sampann Toor Dal', brand: 'Tata Sampann', quantity_value: 1, quantity_unit: 'kg', category: 'staples', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P04', name: 'Amul Toned Milk', brand: 'Amul', quantity_value: 1, quantity_unit: 'L', category: 'dairy', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P05', name: 'Fortune Sunflower Oil', brand: 'Fortune', quantity_value: 1, quantity_unit: 'L', category: 'staples', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P06', name: 'Tomato', brand: '', quantity_value: 1, quantity_unit: 'kg', category: 'produce', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P07', name: 'Potato', brand: '', quantity_value: 1, quantity_unit: 'kg', category: 'produce', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P08', name: 'Amul Curd', brand: 'Amul', quantity_value: 400, quantity_unit: 'g', category: 'dairy', diet_tag: 'veg', listed: true});
MERGE (p:Product {sku: 'P09', name: 'Eggs', brand: '', quantity_value: 12, quantity_unit: 'piece', category: 'eggs', diet_tag: 'non-veg', listed: true});
MERGE (p:Product {sku: 'P10', name: 'Sugar', brand: '', quantity_value: 1, quantity_unit: 'kg', category: 'staples', diet_tag: 'veg', listed: true});

// --- Graph-only products (listed: false) ---
// These exist as graph knowledge only. They become buyable substitutes/variants
// when you add listings for them in the 100-SKU scale-up. match_products must
// check listed=true before proposing one as a buyable substitute.
MERGE (p:Product {sku: 'P11', name: 'Tata Sampann Masoor Dal', brand: 'Tata Sampann', quantity_value: 1, quantity_unit: 'kg', category: 'staples', diet_tag: 'veg', listed: false});
MERGE (p:Product {sku: 'P12', name: 'Fortune Groundnut Oil', brand: 'Fortune', quantity_value: 1, quantity_unit: 'L', category: 'staples', diet_tag: 'veg', listed: false});
MERGE (p:Product {sku: 'P13', name: 'India Gate Sona Masoori Rice', brand: 'India Gate', quantity_value: 1, quantity_unit: 'kg', category: 'staples', diet_tag: 'veg', listed: false});
MERGE (p:Product {sku: 'P14', name: 'Aashirvaad Whole Wheat Atta', brand: 'Aashirvaad', quantity_value: 1, quantity_unit: 'kg', category: 'staples', diet_tag: 'veg', listed: false});
MERGE (p:Product {sku: 'P15', name: 'Amul Toned Milk', brand: 'Amul', quantity_value: 500, quantity_unit: 'ml', category: 'dairy', diet_tag: 'veg', listed: false});

// --- Listings (30) + HAS_LISTING edges: ONE statement per listing ---
MERGE (l:Listing {product_id: 'BLK-ATTA-5KG', platform: 'blinkit', name: 'Aashirvaad Whole Wheat Atta 5kg', price: 289, mrp: 310, in_stock: true, eta_mins: 12, quantity_value: 5, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P01'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-RICE-1KG', platform: 'blinkit', name: 'India Gate Basmati Rice 1kg', price: 145, mrp: 155, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P02'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-DAL-1KG', platform: 'blinkit', name: 'Tata Sampann Toor Dal 1kg', price: 178, mrp: 195, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P03'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-MILK-1L', platform: 'blinkit', name: 'Amul Toned Milk 1L', price: 56, mrp: 60, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P04'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-OIL-1L', platform: 'blinkit', name: 'Fortune Sunflower Oil 1L', price: 168, mrp: 185, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P05'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-TOMATO-1KG', platform: 'blinkit', name: 'Tomato 1kg', price: 38, mrp: 45, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P06'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-POTATO-1KG', platform: 'blinkit', name: 'Potato 1kg', price: 32, mrp: 38, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P07'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-CURD-400G', platform: 'blinkit', name: 'Amul Curd 400g', price: 32, mrp: 35, in_stock: true, eta_mins: 12, quantity_value: 400, quantity_unit: 'g'})
WITH l
MATCH (p:Product {sku: 'P08'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-EGGS-12', platform: 'blinkit', name: 'Eggs 12 pcs', price: 78, mrp: 85, in_stock: true, eta_mins: 12, quantity_value: 12, quantity_unit: 'piece'})
WITH l
MATCH (p:Product {sku: 'P09'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'BLK-SUGAR-1KG', platform: 'blinkit', name: 'Sugar 1kg', price: 48, mrp: 55, in_stock: true, eta_mins: 12, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P10'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-ATTA-5KG', platform: 'zepto', name: 'Aashirvaad Atta - Whole Wheat 5 kg', price: 279, mrp: 310, in_stock: true, eta_mins: 10, quantity_value: 5, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P01'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-RICE-1KG', platform: 'zepto', name: 'India Gate Basmati Rice 1 kg', price: 139, mrp: 155, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P02'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-DAL-1KG', platform: 'zepto', name: 'Tata Sampann Toor Dal 1 kg', price: 182, mrp: 195, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P03'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-MILK-1L', platform: 'zepto', name: 'Amul Toned Milk 1000ml', price: 54, mrp: 60, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P04'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-OIL-1L', platform: 'zepto', name: 'Fortune Sunflower Oil 1 L', price: 175, mrp: 185, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P05'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-TOMATO-1KG', platform: 'zepto', name: 'Tomato 1 kg', price: 42, mrp: 45, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P06'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-POTATO-1KG', platform: 'zepto', name: 'Potato 1 kg', price: 30, mrp: 38, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P07'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-CURD-400G', platform: 'zepto', name: 'Amul Curd 400 g', price: 34, mrp: 35, in_stock: false, eta_mins: 10, quantity_value: 400, quantity_unit: 'g'})
WITH l
MATCH (p:Product {sku: 'P08'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-EGGS-12', platform: 'zepto', name: 'Eggs 12 pcs', price: 82, mrp: 85, in_stock: true, eta_mins: 10, quantity_value: 12, quantity_unit: 'piece'})
WITH l
MATCH (p:Product {sku: 'P09'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'ZEP-SUGAR-1KG', platform: 'zepto', name: 'Sugar 1 kg', price: 45, mrp: 55, in_stock: true, eta_mins: 10, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P10'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-ATTA-5KG', platform: 'instamart', name: 'Whole Wheat Atta 5kg - Aashirvaad', price: 299, mrp: 310, in_stock: true, eta_mins: 25, quantity_value: 5, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P01'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-RICE-1KG', platform: 'instamart', name: 'Basmati Rice 1kg - India Gate', price: 152, mrp: 155, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P02'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-DAL-1KG', platform: 'instamart', name: 'Toor Dal 1kg - Tata Sampann', price: 172, mrp: 195, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P03'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-MILK-1L', platform: 'instamart', name: 'Toned Milk 1 L - Amul', price: 58, mrp: 60, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P04'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-OIL-1L', platform: 'instamart', name: 'Sunflower Oil 1L - Fortune', price: 162, mrp: 185, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'L'})
WITH l
MATCH (p:Product {sku: 'P05'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-TOMATO-1KG', platform: 'instamart', name: 'Tomato 1kg', price: 35, mrp: 45, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P06'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-POTATO-1KG', platform: 'instamart', name: 'Potato 1kg', price: 36, mrp: 38, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P07'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-CURD-400G', platform: 'instamart', name: 'Curd 400g - Amul', price: 30, mrp: 35, in_stock: true, eta_mins: 25, quantity_value: 400, quantity_unit: 'g'})
WITH l
MATCH (p:Product {sku: 'P08'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-EGGS-12', platform: 'instamart', name: 'Eggs 12 pcs', price: 75, mrp: 85, in_stock: false, eta_mins: 25, quantity_value: 12, quantity_unit: 'piece'})
WITH l
MATCH (p:Product {sku: 'P09'})
MERGE (p)-[:HAS_LISTING]->(l);
MERGE (l:Listing {product_id: 'INS-SUGAR-1KG', platform: 'instamart', name: 'Sugar 1kg', price: 52, mrp: 55, in_stock: true, eta_mins: 25, quantity_value: 1, quantity_unit: 'kg'})
WITH l
MATCH (p:Product {sku: 'P10'})
MERGE (p)-[:HAS_LISTING]->(l);

// --- SAME_AS between listings of the same product (one edge per pair, 3 per product = 30) ---
// Query them undirected: MATCH (a:Listing)-[:SAME_AS]-(b:Listing)
MATCH (a:Listing {product_id: 'BLK-ATTA-5KG'}), (b:Listing {product_id: 'INS-ATTA-5KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-ATTA-5KG'}), (b:Listing {product_id: 'ZEP-ATTA-5KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-ATTA-5KG'}), (b:Listing {product_id: 'ZEP-ATTA-5KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-RICE-1KG'}), (b:Listing {product_id: 'INS-RICE-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-RICE-1KG'}), (b:Listing {product_id: 'ZEP-RICE-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-RICE-1KG'}), (b:Listing {product_id: 'ZEP-RICE-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-DAL-1KG'}), (b:Listing {product_id: 'INS-DAL-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-DAL-1KG'}), (b:Listing {product_id: 'ZEP-DAL-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-DAL-1KG'}), (b:Listing {product_id: 'ZEP-DAL-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-MILK-1L'}), (b:Listing {product_id: 'INS-MILK-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-MILK-1L'}), (b:Listing {product_id: 'ZEP-MILK-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-MILK-1L'}), (b:Listing {product_id: 'ZEP-MILK-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-OIL-1L'}), (b:Listing {product_id: 'INS-OIL-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-OIL-1L'}), (b:Listing {product_id: 'ZEP-OIL-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-OIL-1L'}), (b:Listing {product_id: 'ZEP-OIL-1L'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-TOMATO-1KG'}), (b:Listing {product_id: 'INS-TOMATO-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-TOMATO-1KG'}), (b:Listing {product_id: 'ZEP-TOMATO-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-TOMATO-1KG'}), (b:Listing {product_id: 'ZEP-TOMATO-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-POTATO-1KG'}), (b:Listing {product_id: 'INS-POTATO-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-POTATO-1KG'}), (b:Listing {product_id: 'ZEP-POTATO-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-POTATO-1KG'}), (b:Listing {product_id: 'ZEP-POTATO-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-CURD-400G'}), (b:Listing {product_id: 'INS-CURD-400G'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-CURD-400G'}), (b:Listing {product_id: 'ZEP-CURD-400G'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-CURD-400G'}), (b:Listing {product_id: 'ZEP-CURD-400G'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-EGGS-12'}), (b:Listing {product_id: 'INS-EGGS-12'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-EGGS-12'}), (b:Listing {product_id: 'ZEP-EGGS-12'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-EGGS-12'}), (b:Listing {product_id: 'ZEP-EGGS-12'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-SUGAR-1KG'}), (b:Listing {product_id: 'INS-SUGAR-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'BLK-SUGAR-1KG'}), (b:Listing {product_id: 'ZEP-SUGAR-1KG'}) MERGE (a)-[:SAME_AS]->(b);
MATCH (a:Listing {product_id: 'INS-SUGAR-1KG'}), (b:Listing {product_id: 'ZEP-SUGAR-1KG'}) MERGE (a)-[:SAME_AS]->(b);

// --- SUBSTITUTE_FOR (Product -> Product, with confidence) ---
// Confidence bands from the plan: >=0.85 auto-accept, 0.60-0.84 show both, <0.60 ask user.
MATCH (a:Product {sku: 'P03'}), (b:Product {sku: 'P11'}) MERGE (a)-[:SUBSTITUTE_FOR {confidence: 0.78, reason: 'same category, similar cook time'}]->(b);
MATCH (a:Product {sku: 'P05'}), (b:Product {sku: 'P12'}) MERGE (a)-[:SUBSTITUTE_FOR {confidence: 0.75, reason: 'same use, different oil'}]->(b);
MATCH (a:Product {sku: 'P02'}), (b:Product {sku: 'P13'}) MERGE (a)-[:SUBSTITUTE_FOR {confidence: 0.72, reason: 'same category, everyday rice'}]->(b);

// --- VARIANT_OF (pack-size variants, graph-only until listed) ---
MATCH (v:Product {sku: 'P14'}), (b:Product {sku: 'P01'}) MERGE (v)-[:VARIANT_OF]->(b);
MATCH (v:Product {sku: 'P15'}), (b:Product {sku: 'P04'}) MERGE (v)-[:VARIANT_OF]->(b);

// --- Test queries (run after seeding) ---
// MATCH (p:Product) RETURN count(p) AS products;              // expect 15 (10 listed + 5 graph-only)
// MATCH (l:Listing) RETURN count(l) AS listings;              // expect 30
// MATCH ()-[r:SAME_AS]->() RETURN count(r) AS same_as;        // expect 30
// MATCH (p:Product {sku: 'P01'})-[:HAS_LISTING]->(l) RETURN l.platform, l.name, l.price;
// MATCH (l:Listing {product_id: 'ZEP-CURD-400G'})<-[:HAS_LISTING]-(p:Product)-[s:SUBSTITUTE_FOR]->(sub:Product)
// WHERE l.in_stock = false RETURN p.name, sub.name, s.confidence, sub.listed;
// MATCH (p:Product {sku: 'P04'})-[:HAS_LISTING]->(l) RETURN l.platform, l.price, l.in_stock;
