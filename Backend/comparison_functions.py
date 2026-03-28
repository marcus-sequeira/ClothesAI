import json
import logging
from google.cloud import firestore

# ==============================
# 🔧 CONFIG
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"

# ==============================
# 🎨 COLOR FAMILIES (For Smart Matching)
# ==============================
# Define which colors are acceptable "close" matches to prevent the dealbreaker penalty.
COLOR_FAMILIES = {
    "red": ["red", "orange", "burgundy", "maroon", "coral"],
    "orange": ["orange", "red", "yellow", "coral", "peach"],
    "blue": ["blue", "navy", "cyan", "teal", "light blue", "denim"],
    "navy": ["navy", "blue", "black", "dark purple"],
    "black": ["black", "charcoal", "navy", "dark grey"],
    "white": ["white", "cream", "ivory", "off-white"],
    "green": ["green", "olive", "khaki", "mint", "emerald"],
    "yellow": ["yellow", "mustard", "gold", "orange"],
    "pink": ["pink", "rose", "magenta", "fuchsia", "salmon"],
    "purple": ["purple", "lilac", "violet", "lavender", "plum"],
    "grey": ["grey", "gray", "silver", "charcoal", "slate"],
    "brown": ["brown", "tan", "beige", "camel", "chocolate", "khaki"]
}

# ==============================
# 🪵 LOGGING
# ==============================
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Initialize Firestore Client
firestore_client = firestore.Client(project=GCP_PROJECT_ID)


# ==============================
# 🧠 MATCHING ALGORITHM
# ==============================
def avaliar_match_chave_a_chave(master, input_):
    score = 0.0
    max_score = 0.0
    details = {}

    # 🎯 BASE WEIGHTS
    BASE_WEIGHTS = {
        "specific_type": 20,
        "item_category": 15,
        "primary_color": 20,
        "secondary_colors": 5,
        "fit_silhouette": 10,
        "fabric_material": 10,
        "aesthetic_style": 10,
        "typography_text": 10,
        "neckline_collar": 5,
        "sleeve_style": 5,
    }

    # 🧠 DYNAMIC WEIGHT ADJUSTMENT
    dynamic_weights = BASE_WEIGHTS.copy()

    # Check color early for strategy
    master_color = str(master.get("primary_color", "")).strip().lower()
    input_color = str(input_.get("primary_color", "")).strip().lower()

    redistribute_pool = 0

    if master_color and input_color and master_color == input_color:
        # 🎨 Reduce color dominance if already matched
        reduction_factor = 0.5
        original = dynamic_weights["primary_color"]
        reduced = original * reduction_factor
        redistribute_pool = original - reduced
        dynamic_weights["primary_color"] = reduced

    # 🧠 REDISTRIBUTE weight to important attributes
    if redistribute_pool > 0:
        dynamic_weights["specific_type"] += redistribute_pool * 0.4
        dynamic_weights["item_category"] += redistribute_pool * 0.3
        dynamic_weights["fit_silhouette"] += redistribute_pool * 0.3

    # 🚫 Remove weight if field missing in both
    for field in list(dynamic_weights.keys()):
        if not master.get(field) and not input_.get(field):
            dynamic_weights[field] = 0

    # ---------------- MATCH FUNCTIONS ---------------- #

    def add_exact(field):
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight
        a, b = master.get(field), input_.get(field)

        if a and b and str(a).lower() == str(b).lower():
            score += weight
            details[field] = "exact_match"
        else:
            details[field] = "no_match"

    def add_partial_text(field):
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight
        a, b = master.get(field), input_.get(field)

        if a and b:
            a, b = str(a).lower(), str(b).lower()
            if a in b or b in a:
                score += weight
                details[field] = "partial_match"
            else:
                details[field] = "no_match"
        else:
            details[field] = "missing"

    def add_color(field):
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight

        a = str(master.get(field, "")).strip().lower()
        b = str(input_.get(field, "")).strip().lower()

        if a and b and a != "none" and b != "none":
            if a == b:
                score += weight
                details[field] = "match"
            elif b in COLOR_FAMILIES.get(a, []) or a in COLOR_FAMILIES.get(b, []):
                score += (weight * 0.5)
                details[field] = "partial_match"
            else:
                details[field] = "mismatch"
        else:
            details[field] = "missing"

    def add_list_overlap(field):
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight
        a_list = set(master.get(field, []))
        b_list = set(input_.get(field, []))

        if a_list and b_list:
            overlap = len(a_list.intersection(b_list))
            total = len(a_list.union(b_list))
            ratio = overlap / total if total > 0 else 0
            score += weight * ratio
            details[field] = {"overlap": overlap, "ratio": round(ratio, 2)}
        else:
            details[field] = "missing"

    def add_fit(field):
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight
        a, b = master.get(field), input_.get(field)

        if a and b and str(a).strip() and str(b).strip():
            if str(a).split()[0].lower() == str(b).split()[0].lower():
                score += weight
                details[field] = "fit_match"
            else:
                details[field] = "fit_mismatch"
        else:
            details[field] = "missing"

    # ---------------- APPLY SCORING ---------------- #

    add_exact("specific_type")
    add_exact("item_category")
    add_color("primary_color")
    add_exact("secondary_colors")
    add_fit("fit_silhouette")
    add_exact("fabric_material")
    add_list_overlap("aesthetic_style")
    add_partial_text("typography_text")
    add_exact("neckline_collar")
    add_exact("sleeve_style")

    # ---------------- FINAL SCORE ---------------- #

    final_score = int((score / max_score) * 100) if max_score > 0 else 0

    # 🚫 HARD PENALTY (dealbreaker)
    if details.get("primary_color") == "mismatch":
        final_score = int(final_score * 0.2)

    return {
        "score": final_score,
        "raw_score": round(score, 2),
        "max_score": round(max_score, 2),
        "details": details,
        "weights_used": {k: round(v, 2) for k, v in dynamic_weights.items()}
    }

def compare_all(master_data, input_data):
    results = {}
    for input_name, input_json in input_data.items():
        logger.info(f"🔍 Evaluating photo: {input_name}")
        best_match, best_score = None, -1
        comparisons = []

        for master_name, master_json in master_data.items():
            try:
                result = avaliar_match_chave_a_chave(master_json, input_json)
                score = result.get("score", 0)
                comparisons.append({"master": master_name, "score": score, "details": result.get("details")})

                if score > best_score:
                    best_score = score
                    best_match = master_name
            except Exception as e:
                logger.error(f"⚠️ Error comparing {input_name} vs {master_name}: {e}")

        results[input_name] = {
            "best_match": best_match,
            "best_score": best_score,
            "all_comparisons": comparisons
        }
        logger.info(f"   ✅ Best match: {best_match} (Score: {best_score}%)")

    return results


# ==============================
# ☁️ FIRESTORE BATCH FETCH
# ==============================
def fetch_collection_data(collection_name: str) -> dict:
    """Fetches all documents from a Firestore collection into a dictionary."""
    docs = firestore_client.collection(collection_name).stream()
    data = {}
    for doc in docs:
        data[doc.id] = doc.to_dict()
    return data


def save_results_to_firestore(results: dict, results_collection: str = "match_results"):
    """Saves the final match results back to a new Firestore collection."""
    for input_name, result_data in results.items():
        firestore_client.collection(results_collection).document(input_name).set(result_data)
    logger.info(f"💾 Saved {len(results)} match results to Firestore collection: '{results_collection}'")


# ==============================
# 🚀 MAIN
# ==============================
if __name__ == '__main__':
    logger.info("📥 Fetching Master data from Firestore...")
    master_data = fetch_collection_data("garments_master")

    logger.info("📥 Fetching Photo data from Firestore...")
    input_data = fetch_collection_data("garments_taken_photos")

    if not master_data or not input_data:
        logger.warning("⚠️ Missing data! Make sure both 'garments_master' and 'garments_taken_photos' have documents.")
    else:
        logger.info(f"🚀 Starting batch comparison: {len(input_data)} photos vs {len(master_data)} masters...\n")

        # Run the batch comparison
        final_results = compare_all(master_data, input_data)

        # Print a quick summary
        print("\n📊 BATCH MATCHING SUMMARY:")
        for photo_id, res in final_results.items():
            print(f" - {photo_id}  -->  {res['best_match']} ({res['best_score']}%)")

        # Optional: Save the results back to Firestore so you have a record of the matches!
        print("\n")
        save_results_to_firestore(final_results, "garments_matches")