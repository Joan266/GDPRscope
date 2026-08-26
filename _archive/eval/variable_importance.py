"""
Variable importance analysis for GDPR fine prediction.
Measures eta-squared (% of variance explained) for each factor.
"""
import os
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")
conn = psycopg.connect(DATABASE_URL, autocommit=True)
cur = conn.cursor()

# Total variance (log scale)
cur.execute("SELECT variance(log(fine_amount+1)), count(*) FROM documents WHERE fine_amount > 0")
total_var, total_n = cur.fetchone()
total_var = float(total_var)
print(f"Total log-fine variance: {total_var:.4f}  (n={total_n})")
print()
print("=" * 75)
print("VARIABLE IMPORTANCE (eta-squared: % of variance explained)")
print("Higher = more predictive. 0% = useless. 100% = perfect predictor.")
print("=" * 75)


def eta_squared(query: str, label: str) -> None:
    cur.execute(query)
    r = cur.fetchone()
    if r and r[0]:
        eta = float(r[0]) / total_var * 100
        strength = "STRONG" if eta > 10 else "MODERATE" if eta > 5 else "WEAK"
        print(f"  {label:40s}: eta2={eta:5.1f}%  <- {strength}")
    else:
        print(f"  {label:40s}: N/A (insufficient data)")


# 1. JURISDICTION
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT jurisdiction, avg(log(fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM documents WHERE fine_amount > 0 AND jurisdiction IS NOT NULL
        GROUP BY jurisdiction HAVING count(*) >= 5
    ) sub
""", "1. JURISDICTION (country/DPA)")

# 2. SECTOR
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT sector, avg(log(fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM documents WHERE fine_amount > 0 AND sector IS NOT NULL
        GROUP BY sector HAVING count(*) >= 10
    ) sub
""", "2. SECTOR")

# 3. ARTICLE
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT art, avg(log(fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM (SELECT unnest(gdpr_articles) as art, fine_amount FROM documents WHERE fine_amount > 0) e
        GROUP BY art HAVING count(*) >= 10
    ) sub
""", "3. GDPR ARTICLE violated")

# 4. YEAR
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT decision_year, avg(log(fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM documents WHERE fine_amount > 0 AND decision_year IS NOT NULL
        GROUP BY decision_year HAVING count(*) >= 5
    ) sub
""", "4. YEAR of decision")

# 5. NUMBER OF ARTICLES violated
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT n_arts, avg(log_fine) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM (
            SELECT array_length(gdpr_articles, 1) as n_arts, log(fine_amount+1) as log_fine
            FROM documents WHERE fine_amount > 0 AND gdpr_articles IS NOT NULL
        ) sub GROUP BY n_arts HAVING count(*) >= 5
    ) sub2
""", "5. NUMBER of articles violated")

# 6. DATA SOURCE
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT source, avg(log(fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM documents WHERE fine_amount > 0
        GROUP BY source HAVING count(*) >= 5
    ) sub
""", "6. DATA SOURCE (tracker vs gdprhub)")

# 7. COOPERATION
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT CASE WHEN (cf.factor_f_cooperation->>'cooperated')::boolean THEN 'Y' ELSE 'N' END as coop,
               avg(log(d.fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM case_factors cf JOIN documents d ON d.id = cf.document_id
        WHERE d.fine_amount > 0 AND cf.factor_f_cooperation->>'cooperated' IS NOT NULL
        GROUP BY 1
    ) sub
""", "7. COOPERATION with DPA")

# 8. INTENT
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT cf.factor_b_intent->>'type' as intent,
               avg(log(d.fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM case_factors cf JOIN documents d ON d.id = cf.document_id
        WHERE d.fine_amount > 0 AND cf.factor_b_intent->>'type' IN ('intentional', 'negligent')
        GROUP BY 1
    ) sub
""", "8. INTENT (intentional vs negligent)")

# 9. SENSITIVE DATA
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT CASE WHEN (cf.factor_g_data_categories->>'sensitive')::boolean THEN 'Y' ELSE 'N' END,
               avg(log(d.fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM case_factors cf JOIN documents d ON d.id = cf.document_id
        WHERE d.fine_amount > 0 AND cf.factor_g_data_categories->>'sensitive' IS NOT NULL
        GROUP BY 1
    ) sub
""", "9. SENSITIVE DATA categories")

# 10. SELF-REPORTED
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT CASE WHEN (cf.factor_h_discovery->>'self_reported')::boolean THEN 'Y' ELSE 'N' END,
               avg(log(d.fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM case_factors cf JOIN documents d ON d.id = cf.document_id
        WHERE d.fine_amount > 0 AND cf.factor_h_discovery->>'self_reported' IS NOT NULL
        GROUP BY 1
    ) sub
""", "10. SELF-REPORTED (voluntary notification)")

# 11. CORRECTIVE MEASURES
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT CASE WHEN (cf.factor_c_mitigation->>'mitigated')::boolean THEN 'Y' ELSE 'N' END,
               avg(log(d.fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM case_factors cf JOIN documents d ON d.id = cf.document_id
        WHERE d.fine_amount > 0 AND cf.factor_c_mitigation->>'mitigated' IS NOT NULL
        GROUP BY 1
    ) sub
""", "11. CORRECTIVE MEASURES taken")

# 12. PRIOR VIOLATIONS
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT CASE WHEN cf.factor_e_prior_violations IS NOT NULL
                    AND cf.factor_e_prior_violations::text LIKE '%true%' THEN 'Y' ELSE 'N' END,
               avg(log(d.fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM case_factors cf JOIN documents d ON d.id = cf.document_id
        WHERE d.fine_amount > 0
        GROUP BY 1
    ) sub
""", "12. PRIOR VIOLATIONS (recidivism)")

# ── Combined: jurisdiction + sector ──
print()
print("=" * 75)
print("COMBINED FACTORS")
print("=" * 75)
eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT jurisdiction || '|' || sector as combo,
               avg(log(fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM documents
        WHERE fine_amount > 0 AND jurisdiction IS NOT NULL AND sector IS NOT NULL
        GROUP BY 1 HAVING count(*) >= 5
    ) sub
""", "JURISDICTION + SECTOR combined")

eta_squared("""
    SELECT sum(n * power(grp_mean - global_mean, 2)) / sum(n)
    FROM (
        SELECT jurisdiction || '|' || art as combo,
               avg(log(fine_amount+1)) as grp_mean, count(*) as n,
               (SELECT avg(log(fine_amount+1)) FROM documents WHERE fine_amount > 0) as global_mean
        FROM (
            SELECT jurisdiction, unnest(gdpr_articles) as art, fine_amount
            FROM documents WHERE fine_amount > 0 AND jurisdiction IS NOT NULL
        ) e GROUP BY 1 HAVING count(*) >= 10
    ) sub
""", "JURISDICTION + ARTICLE combined")

# ── What we're MISSING ──
print()
print("=" * 75)
print("MISSING VARIABLES (estimated importance)")
print("=" * 75)
print("  TURNOVER / COMPANY SIZE          : ~30-50% (estimated)")
print("    -> Biggest missing variable. Explains why IKEA Romania (1K)")
print("       and IKEA Austria (1.5M) get same articles but 1500x diff fine.")
print("    -> EDPB methodology explicitly uses turnover as Step 2 basis.")
print("    -> Rarely public in decisions. User can provide it in our form.")
print()
print("  DATA SUBJECTS AFFECTED           : ~5-10% (estimated)")
print("    -> Art.83(2)(a) factor. Rarely structured in our data.")
print("    -> User can provide it in our form.")
print()
print("  DURATION OF VIOLATION            : ~2-5% (estimated)")
print("    -> Art.83(2)(a) factor. Available in case_factors for 765 docs.")
print()
print("  PRIOR DPA ORDERS IGNORED         : ~3-8% (estimated)")
print("    -> Art.83(2)(i). Aggravating. Not tracked in our schema.")

conn.close()
