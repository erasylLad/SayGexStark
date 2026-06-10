import os
# pyrefly: ignore [missing-import]
import fitz  # PyMuPDF
import chromadb
from chromadb.utils import embedding_functions

def extract_text_from_pdf(file_path: str) -> str:
    """Извлекает только нативный цифровой текст. Сканы и фото пропускаются."""
    print(f" Анализ текстового слоя файла: {os.path.basename(file_path)}")
    full_text = []
    
    try:
        doc = fitz.open(file_path)
        for page_idx, page in enumerate(doc):
            page_text = page.get_text() or ""
            if page_text.strip():
                full_text.append(page_text)
        doc.close()
    except Exception as e:
        print(f"❌ Ошибка при чтении PDF {os.path.basename(file_path)}: {e}")
        
    return "\n\n".join(full_text)


def run_ingestion():
    print("📦 [RAG INGEST] Запуск встроенной индексации (только цифровой текст)...")
    
    try:
        client = chromadb.PersistentClient(path="/chroma_db")
        emb_fn = embedding_functions.DefaultEmbeddingFunction()
        
        try:
            client.delete_collection(name="kmg_vnd")
        except Exception:
            pass
            
        collection = client.create_collection(name="kmg_vnd", embedding_function=emb_fn)
    except Exception as e:
        print(f"❌ Ошибка инициализации ChromaDB: {e}")
        return

    docs_dir = "docs"
    os.makedirs(docs_dir, exist_ok=True)
    
    if not os.listdir(docs_dir):
        print(f"⚠️ Папка '{docs_dir}' пуста. Скопируй туда свои PDF файлы КМГ!")
        return

    id_counter = 0
    for filename in os.listdir(docs_dir):
        file_path = os.path.join(docs_dir, filename)
        content = ""
        
        if filename.endswith(".txt"):
            print(f"📄 Чтение текстового файла: {filename}")
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        elif filename.endswith(".pdf"):
            print(f"📕 Чтение PDF-документа: {filename}")
            content = extract_text_from_pdf(file_path)

        if not content.strip():
            print(f"⏩ Файл {filename} пропущен (нет доступного цифрового текста).")
            continue

        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip() and len(p.strip()) > 10]
        for p in paragraphs:
            id_counter += 1
            collection.add(
                documents=[p],
                metadatas=[{"source": filename}],
                ids=[f"doc_chunk_{id_counter}"]
            )

    print(f"✅ Индексация завершена! В ChromaDB загружено {id_counter} блоков данных.")

if __name__ == "__main__":
    run_ingestion()