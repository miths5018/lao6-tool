from flask import Flask, render_template, request, send_file, jsonify
import os, tempfile, re, unicodedata, threading, uuid

app = Flask(__name__)
app.secret_key = "super_secret_key"

BASE_DIR = os.path.dirname(__file__)
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ====== 工具函数 ======
def clean_line(line: str) -> str:
    if not line:
        return ""
    line = re.sub(r"[\u200b\u200e\u200f\uFEFF\s\u00a0\t]", "", line)
    line = line.lstrip("+@")
    line = unicodedata.normalize('NFKC', line)
    return line.lower()

ENABLE_BLACKLIST = False
blacklist = ["haihua","chuhai","benchi","818","databox","dolphin","diggoldsl","juejin"]

def is_valid_username(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    if n.endswith("bot"):
        return False
    if ENABLE_BLACKLIST:
        if 'bot' in n:
            return False
        for word in blacklist:
            if word in n:
                return False
    return True

# ====== 任务存储 ======
tasks = {}

def update_progress(task_id, percent):
    if task_id in tasks:
        tasks[task_id]["progress"] = percent

# ====== 处理函数 ======
def process_merge_task(file_paths, task_id):
    seen = set()
    total_source = sum(1 for path in file_paths for _ in open(path,"r",encoding="utf-8-sig"))
    output_file = tempfile.NamedTemporaryFile(delete=False, suffix="_merge.txt", mode="w", encoding="utf-8")
    done = 0
    for path in file_paths:
        with open(path,"r",encoding="utf-8-sig") as f:
            for line in f:
                done += 1
                c = clean_line(line)
                if is_valid_username(c) and c not in seen:
                    seen.add(c)
                    output_file.write(c+"\n")
                if done % 500 == 0:
                    update_progress(task_id, int(done/total_source*100))
    output_file.close()
    tasks[task_id].update({
        "file": output_file.name, 
        "progress":100, 
        "count_total": len(seen), 
        "count_source": total_source
    })

def process_compare_task(file_a_path, file_b_path, task_id):
    set_a, set_b = set(), set()
    total_source = sum(1 for _ in open(file_a_path,"r",encoding="utf-8-sig")) + sum(1 for _ in open(file_b_path,"r",encoding="utf-8-sig"))
    done = 0
    for path, target_set in [(file_a_path, set_a), (file_b_path, set_b)]:
        with open(path,"r",encoding="utf-8-sig") as f:
            for line in f:
                done += 1
                c = clean_line(line)
                if is_valid_username(c):
                    target_set.add(c)
                if done % 500 == 0:
                    update_progress(task_id, int(done/total_source*50))
    unique_a = set_a - set_b
    unique_b = set_b - set_a
    out_a = tempfile.NamedTemporaryFile(delete=False, suffix="_A.txt", mode="w", encoding="utf-8")
    out_b = tempfile.NamedTemporaryFile(delete=False, suffix="_B.txt", mode="w", encoding="utf-8")
    for line in sorted(unique_a): out_a.write(line+"\n")
    for line in sorted(unique_b): out_b.write(line+"\n")
    out_a.close()
    out_b.close()
    tasks[task_id].update({
        "file": {"A": out_a.name, "B": out_b.name}, 
        "progress":100, 
        "count_total": len(unique_a)+len(unique_b), 
        "count_source": total_source
    })

def process_username_task(file_path, task_id):
    seen = set()
    total_source = sum(1 for _ in open(file_path,"r",encoding="utf-8-sig"))
    output_file = tempfile.NamedTemporaryFile(delete=False, suffix="_username.txt", mode="w", encoding="utf-8")
    done = 0
    with open(file_path,"r",encoding="utf-8-sig") as f:
        for line in f:
            done += 1
            c = clean_line(line)
            if is_valid_username(c) and c not in seen:
                seen.add(c)
                output_file.write(c+"\n")
            if done % 500 == 0:
                update_progress(task_id, int(done/total_source*100))
    output_file.close()
    tasks[task_id].update({
        "file": output_file.name,
        "progress":100,
        "count_total": len(seen),
        "count_source": total_source
    })

# US / CA 分类
CANADA_AREA_CODES = { "204","226","236","249","250","289","306","343","365","387","403","416","418","431",
                      "437","438","450","506","514","519","548","579","581","587","600","604","613","639",
                      "647","672","705","709","742","778","780","782","807","819","825","867","873","902","905"}
def classify_number(num: str) -> str:
    n = re.sub(r"\D","",num)
    if len(n) == 11 and n.startswith("1"): area=n[1:4]
    elif len(n) == 10: area=n[:3]
    else: return "OTHER"
    return "CA" if area in CANADA_AREA_CODES else "US"

def process_us_ca_task(file_path, task_id):
    out = {k: tempfile.NamedTemporaryFile(delete=False, suffix=f"_{k}.txt", mode="w", encoding="utf-8") for k in ["US","CA","OTHER"]}
    counts = {"US":0,"CA":0,"OTHER":0}
    total_source = sum(1 for _ in open(file_path,"r",encoding="utf-8-sig"))
    done = 0
    with open(file_path,"r",encoding="utf-8-sig") as f:
        for line in f:
            done += 1
            c = clean_line(line)
            if not c: continue
            r = classify_number(c)
            counts[r] += 1
            out[r].write(c+"\n")
            if done % 500 == 0:
                update_progress(task_id, int(done/total_source*100))
    for f in out.values(): f.close()
    tasks[task_id].update({
        "file": {k:v.name for k,v in out.items()},
        "progress":100,
        "count_US": counts["US"],
        "count_CA": counts["CA"],
        "count_OTHER": counts["OTHER"],
        "count_source": total_source
    })

# ====== 路由 ======
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/merge", methods=["POST"])
def merge():
    files = request.files.getlist("files")
    paths = []
    for f in files:
        path = os.path.join(UPLOAD_FOLDER, f.filename)
        f.save(path)
        paths.append(path)
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status":"processing","progress":0}
    threading.Thread(target=process_merge_task, args=(paths, task_id), daemon=True).start()
    return jsonify({"task_id": task_id})

@app.route("/compare", methods=["POST"])
def compare():
    file_a = request.files.get("file_a")
    file_b = request.files.get("file_b")
    path_a = os.path.join(UPLOAD_FOLDER, file_a.filename)
    path_b = os.path.join(UPLOAD_FOLDER, file_b.filename)
    file_a.save(path_a)
    file_b.save(path_b)
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status":"processing","progress":0}
    threading.Thread(target=process_compare_task, args=(path_a, path_b, task_id), daemon=True).start()
    return jsonify({"task_id": task_id})

@app.route("/username_dedup", methods=["POST"])
def username_dedup():
    file = request.files.get("username_file")
    path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(path)
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status":"processing","progress":0}
    threading.Thread(target=process_username_task, args=(path, task_id), daemon=True).start()
    return jsonify({"task_id": task_id})

@app.route("/us_ca", methods=["POST"])
def us_ca():
    file = request.files.get("us_ca_file")
    path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(path)
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status":"processing","progress":0}
    threading.Thread(target=process_us_ca_task, args=(path, task_id), daemon=True).start()
    return jsonify({"task_id": task_id})

@app.route("/status/<task_id>")
def status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"status":"notfound"})
    if task.get("progress",0)>=100:
        task["status"]="ready"

    # 针对 A/B 对比任务
    if isinstance(task.get("file"), dict) and "A" in task["file"] and "B" in task["file"]:
        count_a = sum(1 for _ in open(task["file"]["A"], "r", encoding="utf-8-sig"))
        count_b = sum(1 for _ in open(task["file"]["B"], "r", encoding="utf-8-sig"))
        return jsonify({
            "status": task.get("status"),
            "progress": task.get("progress",0),
            "count_A": count_a,
            "count_B": count_b,
            "count_source": task.get("count_source",0),
            "download_url_A": f"/download/{task_id}/A",
            "download_url_B": f"/download/{task_id}/B"
        })

    # 针对 US/CA 区分任务
    if isinstance(task.get("file"), dict) and "US" in task["file"] and "CA" in task["file"]:
        return jsonify({
            "status": task.get("status"),
            "progress": task.get("progress",0),
            "count_US": task.get("count_US",0),
            "count_CA": task.get("count_CA",0),
            "count_OTHER": task.get("count_OTHER",0),
            "count_source": task.get("count_source",0),
            "download_url_US": f"/download/{task_id}/US",
            "download_url_CA": f"/download/{task_id}/CA",
            "download_url_OTHER": f"/download/{task_id}/OTHER"
        })

    # 默认任务
    return jsonify({
        "status": task.get("status"),
        "progress": task.get("progress",0),
        "count_total": task.get("count_total",0),
        "count_source": task.get("count_source",0),
        "download_url_default": f"/download/{task_id}" if isinstance(task.get("file"), str) else None
    })

# ========= 下载 =========
@app.route("/download/<task_id>")
@app.route("/download/<task_id>/<key>")
def download(task_id, key=None):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"status":"error","message":"任务不存在"})

    path = None
    filename = "file.txt"
    if key:
        if isinstance(task["file"], dict):
            path = task["file"].get(key)
            filename = f"MG_{sum(1 for _ in open(path,encoding='utf-8-sig'))}.txt"
    else:
        if isinstance(task["file"], str):
            path = task["file"]
            filename = f"MG_{task.get('count_total',0)}.txt"

    if path and os.path.exists(path):
        return send_file(path, as_attachment=True, download_name=filename)
    else:
        return jsonify({"status":"error","message":"文件不存在"})

# ========= 新增接口：首页统计 =========
@app.route("/stats")
def stats():
    total_tasks = sum(
        task.get("count_total", 0) +
        task.get("count_US",0) +
        task.get("count_CA",0) +
        task.get("count_OTHER",0)
        for task in tasks.values()
    )
    return jsonify({
        "total": total_tasks,
        "tools": 4  # 前端显示工具总数，可根据实际修改
    })

if __name__=="__main__":
    app.run(debug=True, port=5000)
