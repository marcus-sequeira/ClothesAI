import tkinter as tk
from tkinter import messagebox
from io import BytesIO
from PIL import Image, ImageTk

from google.cloud import storage, firestore

# ==============================
# 🔧 CONFIG
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"
IMAGE_SIZE = (320, 320)

# ==============================
# ☁️ CLIENTS
# ==============================
storage_client = storage.Client(project=GCP_PROJECT_ID)
firestore_client = firestore.Client(project=GCP_PROJECT_ID)


# ==============================
# 📋 CLIPBOARD
# ==============================
def copy_to_clipboard(text: str, root):
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        messagebox.showinfo("Copied", text)
    except Exception as e:
        print(f"❌ Clipboard error: {e}")


# ==============================
# 📥 LOAD DATA FROM FIRESTORE
# ==============================
def load_results_from_firestore():
    """Fetches the match results from the 'garments_matches' collection."""
    try:
        docs = firestore_client.collection("garments_matches").stream()
        results = {doc.id: doc.to_dict() for doc in docs}
        return results
    except Exception as e:
        print(f"❌ Failed to load results from Firestore: {e}")
        return None


def get_image_uri_from_firestore(collection_name: str, doc_id: str) -> str:
    """Looks up the exact GCS image URI from the garment document."""
    try:
        doc = firestore_client.collection(collection_name).document(doc_id).get()
        if doc.exists:
            return doc.to_dict().get("image_gcs_uri")
    except Exception as e:
        print(f"❌ Error fetching doc {doc_id}: {e}")
    return None


# ==============================
# 🖼️ LOAD IMAGE FROM GCS
# ==============================
def load_image_from_gcs_uri(gcs_uri: str):
    """Downloads an image exactly from its gs:// URI."""
    if not gcs_uri:
        return None

    try:
        # Parse the gs:// bucket and blob
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            return None

        image_bytes = blob.download_as_bytes()
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        img = img.resize(IMAGE_SIZE)

        return ImageTk.PhotoImage(img)

    except Exception as e:
        print(f"❌ Image error for {gcs_uri}: {e}")
        return None


# ==============================
# 🪟 POPUP (WITH COPY PATH)
# ==============================
def open_popup(input_id: str, match_id: str, score: int):
    popup = tk.Toplevel()
    popup.title(f"Match Comparison: {input_id} vs {match_id}")

    # 1. Lookup the exact image URIs from Firestore
    input_path = get_image_uri_from_firestore("garments_taken_photos", input_id)
    match_path = get_image_uri_from_firestore("garments_master", match_id)

    # 2. Download the images
    input_img = load_image_from_gcs_uri(input_path)
    match_img = load_image_from_gcs_uri(match_path)

    # UI Layout
    tk.Label(popup, text=f"INPUT: {input_id}", font=("Arial", 12, "bold")).grid(row=0, column=0)
    tk.Label(popup, text=f"MATCH: {match_id}", font=("Arial", 12, "bold")).grid(row=0, column=1)

    if input_img:
        lbl1 = tk.Label(popup, image=input_img)
        lbl1.image = input_img  # Keep a reference!
        lbl1.grid(row=1, column=0)
    else:
        tk.Label(popup, text="No image found").grid(row=1, column=0)

    if match_img:
        lbl2 = tk.Label(popup, image=match_img)
        lbl2.image = match_img  # Keep a reference!
        lbl2.grid(row=1, column=1)
    else:
        tk.Label(popup, text="No image found").grid(row=1, column=1)

    # 📋 COPY IMAGE PATHS
    if input_path:
        tk.Button(
            popup, text="Copy Input gs:// URI",
            command=lambda: copy_to_clipboard(input_path, popup)
        ).grid(row=2, column=0, pady=5)

    if match_path:
        tk.Button(
            popup, text="Copy Match gs:// URI",
            command=lambda: copy_to_clipboard(match_path, popup)
        ).grid(row=2, column=1, pady=5)

    tk.Label(
        popup,
        text=f"Score: {score}",
        font=("Arial", 14, "bold"),
        fg="green" if score >= 70 else "orange"
    ).grid(row=3, column=0, columnspan=2, pady=10)


# ==============================
# 🪟 ALL MATCHES
# ==============================
def open_all_matches(input_id: str, comparisons: list):
    window = tk.Toplevel()
    window.title(f"All matches for {input_id}")

    sorted_matches = sorted(comparisons, key=lambda x: x["score"], reverse=True)

    row = 0
    for item in sorted_matches:
        match_id = item["master"]
        score = item["score"]

        tk.Label(window, text=match_id, width=30, anchor="w").grid(row=row, column=0, padx=5, pady=2)

        tk.Label(
            window, text=str(score),
            fg="green" if score >= 70 else "orange"
        ).grid(row=row, column=1, padx=5, pady=2)

        tk.Button(
            window, text="View",
            command=lambda i=input_id, m=match_id, s=score: open_popup(i, m, s)
        ).grid(row=row, column=2, padx=5, pady=2)

        row += 1


# ==============================
# 🖥️ MAIN UI
# ==============================
def build_ui(results: dict):
    root = tk.Tk()
    root.title("Clothes Matcher Viewer (Firestore)")

    # Create a canvas and scrollbar for the main container in case of many photos
    canvas = tk.Canvas(root)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    container = tk.Frame(canvas)

    container.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=container, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    row = 0
    for input_id, data in results.items():
        best_match = data.get("best_match")
        score = data.get("best_score", 0)
        comparisons = data.get("all_comparisons", [])

        if not best_match:
            continue

        tk.Label(container, text=input_id, width=30, anchor="w").grid(row=row, column=0, padx=5, pady=5)

        tk.Label(
            container, text=f"{score}%",
            fg="green" if score >= 70 else "orange", font=("Arial", 10, "bold")
        ).grid(row=row, column=1, padx=5, pady=5)

        tk.Button(
            container, text="Best Match",
            command=lambda i=input_id, m=best_match, s=score: open_popup(i, m, s)
        ).grid(row=row, column=2, padx=5, pady=5)

        tk.Button(
            container, text="All Matches",
            command=lambda n=input_id, c=comparisons: open_all_matches(n, c)
        ).grid(row=row, column=3, padx=5, pady=5)

        row += 1

    # Set a reasonable default window size
    root.geometry("600x400")
    root.mainloop()


# ==============================
# 🚀 MAIN
# ==============================
def main():
    try:
        print("📥 Fetching results from Firestore...")
        results = load_results_from_firestore()

        if not results:
            messagebox.showerror("Error",
                                 "No match results found in Firestore collection 'garments_matches'. Run your batch comparison script first!")
            return

        print("🎨 Building UI...")
        build_ui(results)

    except Exception as e:
        messagebox.showerror("Error", str(e))


if __name__ == "__main__":
    main()