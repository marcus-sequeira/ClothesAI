import json
from google.cloud import storage

# ==============================
# 🔧 CONFIG & INIT
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"
BUCKET_NAME = "clothes_app"
BASE_PREFIX = "jsons_data/"
OUTPUT_PATH = "results/matching_results.json"

MIN_SCORE_THRESHOLD = 50
TOP_K = 3

# 1. Initialize GCS Client
storage_client = storage.Client(project=GCP_PROJECT_ID)

# ==============================
# ☁️ GCS HELPER FUNCTIONS
# ==============================
def load_and_split_jsons():
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs(prefix=BASE_PREFIX)

    master_data = {}
    input_data = {}

    for blob in blobs:
        if not blob.name.endswith('.json'):
            continue

        try:
            content = blob.download_as_text(encoding='utf-8')
            json_data = json.loads(content)

            if "/master/" in blob.name:
                master_data[blob.name] = json_data
                print(f"📥 MASTER: {blob.name}")

            elif "/input/" in blob.name:
                input_data[blob.name] = json_data
                print(f"📥 INPUT: {blob.name}")

        except Exception as e:
            print(f"❌ Error loading {blob.name}: {e}")

    return master_data, input_data

def save_results(results):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(OUTPUT_PATH)

    blob.upload_from_string(
        json.dumps(results, indent=2, ensure_ascii=False),
        content_type='application/json'
    )
    print(f"\n💾 Saved to gs://{BUCKET_NAME}/{OUTPUT_PATH}")

# ==============================
# 🧠 MATCHING ENGINE
# ==============================
def extract_analysis(data):
    return data.get("analysis", data)

def avaliar_match(master, input_):
    score = 0

    def match(a, b, weight):
        if not a or not b:
            return 0
        return weight if str(a).lower() == str(b).lower() else 0

    score += match(master.get("specific_type"), input_.get("specific_type"), 30)
    score += match(master.get("item_category"), input_.get("item_category"), 15)

    if master.get("primary_color") and input_.get("primary_color"):
        if master["primary_color"].lower() in input_["primary_color"].lower() \
           or input_["primary_color"].lower() in master["primary_color"].lower():
            score += 20

    if master.get("fit_silhouette") and input_.get("fit_silhouette"):
        if master["fit_silhouette"].split()[0].lower() == input_["fit_silhouette"].split()[0].lower():
            score += 10

    score += match(master.get("fabric_material"), input_.get("fabric_material"), 10)

    master_styles = set(master.get("aesthetic_style", []))
    input_styles = set(input_.get("aesthetic_style", []))
    overlap = master_styles.intersection(input_styles)
    score += len(overlap) * 5

    if master.get("typography_text") and input_.get("typography_text"):
        if master["typography_text"].lower() in input_["typography_text"].lower() \
           or input_["typography_text"].lower() in master["typography_text"].lower():
            score += 15

    MAX_SCORE = 120
    normalized_score = min(int((score / MAX_SCORE) * 100), 100)

    return {
        "score": normalized_score,
        "raw_score": score,
        "matched_styles": list(overlap)
    }

def compare_all(master_data, input_data):
    results = {}

    for input_name, input_json in input_data.items():
        print(f"\n🔍 Processing: {input_name}")
        input_analysis = extract_analysis(input_json)
        comparisons = []

        for master_name, master_json in master_data.items():
            try:
                master_analysis = extract_analysis(master_json)
                result = avaliar_match(master_analysis, input_analysis)

                comparisons.append({
                    "master": master_name,
                    "score": result["score"]
                })
            except Exception as e:
                print(f"⚠️ Error comparing {input_name} vs {master_name}: {e}")

        if not comparisons:
            print(f"⚠️ No masters found to compare against {input_name}")
            continue

        sorted_matches = sorted(comparisons, key=lambda x: x["score"], reverse=True)

        best_match = sorted_matches[0]["master"]
        best_score = sorted_matches[0]["score"]

        if best_score < MIN_SCORE_THRESHOLD:
            best_match = None

        results[input_name] = {
            "best_match": best_match,
            "best_score": best_score,
            "top_matches": sorted_matches[:TOP_K]
        }
        print(f"✅ Best: {best_match} ({best_score})")

    return results

# ==============================
# 🚀 MAIN PIPELINE
# ==============================
if __name__ == '__main__':
    try:
        print("📂 Loading data from GCS...")
        master_data, input_data = load_and_split_jsons()

        print(f"\n✅ Masters: {len(master_data)}")
        print(f"✅ Inputs: {len(input_data)}")

        print("\n🚀 Running matching engine...")
        final_results = compare_all(master_data, input_data)

        if final_results:
            save_results(final_results)
            print("\n🎯 DONE!")
        else:
            print("\n⚠️ No results generated to save.")

    except Exception as e:
        print(f"❌ Fatal error: {e}")