import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path

# Import theme từ theme.py
import theme

st.set_page_config(
    page_title="Dashboard Kỹ năng Tuyển dụng IT",
    page_icon="📊",
    layout="wide"
)

# Áp dụng giao diện và bảng màu chuẩn từ theme.py
theme.apply(st)
BASE = Path(__file__).parent
# Sửa lại nếu app.py nằm chỗ khác; có thể dùng đường dẫn tuyệt đối:
# RESULTS_DIR = Path(r"D:\KLTN\Final\KLTN_MIS\clean_out\results")
RESULTS_DIR = Path(r"D:\KLTN\Final\KLTN_MIS\clean_out\results")

FILES = {
    "rq1_core": "rq1_out/rq1_core_stack.csv",
    "rq1_breadth": "rq1_out/rq1_breadth.csv",
    "rq2_density": "rq2_out/rq2_density.csv",
    "rq3_table": "rq3_out/rq3_pooled_table.csv",
    "rq4_city": "rq4_out/rq4_city_level.csv",
    "cooc_clusters": "cooc_out/cooc_clusters.csv",
    "cooc_top_pairs": "cooc_out/cooc_top_pairs.csv",
    "cooc_pairs": "cooc_out/cooc_pairs.csv",
}


@st.cache_data
def load_data():
    data, missing = {}, []
    for key, rel in FILES.items():
        p = RESULTS_DIR / rel
        if p.exists():
            data[key] = pd.read_csv(p)
        else:
            data[key] = None
            missing.append(str(p))
    return data, missing


data, missing = load_data()
if missing:
    st.error("Không tìm thấy file:\n\n" + "\n".join(missing))

st.sidebar.title("🧭 Điều hướng Dashboard")
section = st.sidebar.radio(
    "Chọn nội dung phân tích:",
    [
        "1. Core Stack & Độ rộng (RQ1)",
        "2. Mật độ Cloud, AI & Ngoại ngữ (RQ2)",
        "3. Biến thiên theo Kinh nghiệm (RQ3)",
        "4. Phân bố Địa lý & Cấp bậc (RQ4)",
        "5. Cụm & Cặp đồng xuất hiện (Co-occurrence)"
    ]
)

st.title(" Dashboard Phân tích Dữ liệu Tuyển dụng IT")
st.markdown("---")

# 1. RQ1: Core Stack & Breadth
if section.startswith("1"):
    st.header("1. Tổ hợp Kỹ năng Cốt lõi (Core Stack) & Độ rộng Kỹ năng")
    df_core = data.get("rq1_core")
    df_breadth = data.get("rq1_breadth")
    
    if df_core is not None:
        roles = df_core["role"].unique()
        selected_role = st.selectbox("Chọn vị trí công nghệ:", roles)
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f"Top Core Skills cho {selected_role}")
            sub_core = df_core[(df_core["role"] == selected_role) & (df_core["is_core"] == True)].sort_values(by="pct", ascending=True)
            if not sub_core.empty:
                fig = px.bar(
                    sub_core, x="pct", y="skill", orientation="h",
                    title=f"Tỷ lệ xuất hiện của Core Skills ({selected_role})",
                    labels={"pct": "Tỷ lệ (%)", "skill": "Kỹ năng"},
                    text="pct"
                )
                fig.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Không có dữ liệu core stack cho vị trí này.")
        
        with col2:
            st.subheader("So sánh Độ rộng Kỹ năng (Hard Skills Mean)")
            if df_breadth is not None:
                fig2 = px.bar(
    df_breadth[df_breadth["source"] != df_breadth["source"][df_breadth["source"].str.contains("TẤT CẢ", na=False)].iloc[0]],
    x="role", y="n_hard_skills_mean", color="source", barmode="group",
    labels={"n_hard_skills_mean": "Số kỹ năng cứng trung bình", "role": "Vị trí", "source": "Nguồn"},
    title="Số kỹ năng cứng trung bình theo vị trí và nguồn",
)
                st.plotly_chart(fig2, use_container_width=True)

# 2. RQ2: Density
elif section.startswith("2"):
    st.header("2. Mật độ xuất hiện Cloud/DevOps, AI/LLM và Ngoại ngữ")
    df_density = data.get("rq2_density")
    if df_density is not None:
        tech_groups = df_density["tech_group"].unique()
        selected_group = st.selectbox("Chọn nhóm công nghệ / kỹ năng phụ trợ:", tech_groups)
        
        sub_density = df_density[df_density["tech_group"] == selected_group].sort_values(by="pct", ascending=False)
        fig = px.bar(
            sub_density, x="role", y="pct",
            title=f"Mật độ xuất hiện nhóm: {selected_group}",
            labels={"pct": "Tỷ lệ (%)", "role": "Vị trí"},
            color="role", text="pct"
        )
        fig.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        st.plotly_chart(fig, use_container_width=True)

# 3. RQ3: Experience Bands
elif section.startswith("3"):
    st.header("3. Biến thiên Độ rộng Kỹ năng theo Mốc Kinh nghiệm")
    df_table = data.get("rq3_table")
    if df_table is not None:
        roles_rq3 = df_table["role"].unique()
        selected_roles = st.multiselect("Chọn vị trí để so sánh:", roles_rq3, default=list(roles_rq3))
        
        sub_table = df_table[df_table["role"].isin(selected_roles)]
        fig = px.line(
            sub_table, x="band", y="n_hard_skills_mean", color="role",
            markers=True, title="Số lượng Kỹ năng cứng trung bình theo Cấp bậc kinh nghiệm",
            labels={"band": "Mốc kinh nghiệm", "n_hard_skills_mean": "Số lượng kỹ năng cứng trung bình", "role": "Vị trí"}
        )
        st.plotly_chart(fig, use_container_width=True)
        
        st.dataframe(sub_table)

# 4. RQ4: Geography
elif section.startswith("4"):
    st.header("4. Phân bố Địa lý & Cấp bậc")
    df_city = data.get("rq4_city")
    if df_city is not None:
        levels = df_city["level"].unique()
        selected_level = st.selectbox("Chọn cấp bậc (Level):", levels)
        
        sub_city = df_city[df_city["level"] == selected_level].sort_values(by="pct_of_city_jobs", ascending=False)
        fig = px.bar(
            sub_city.head(10), x="city", y="pct_of_city_jobs",
            title=f"Top thành phố tuyển dụng cho cấp bậc: {selected_level}",
            labels={"pct_of_city_jobs": "Tỷ lệ việc làm tại thành phố (%)", "city": "Thành phố"},
            color="city"
        )
        st.plotly_chart(fig, use_container_width=True)

# 5. Co-occurrence & Clusters
elif section.startswith("5"):
    st.header("5. Cụm & Cặp kỹ năng đồng xuất hiện (Co-occurrence)")
    df_pairs = data.get("cooc_top_pairs")
    df_clusters = data.get("cooc_clusters")
    
    tab1, tab2 = st.tabs(["Cặp kỹ năng hàng đầu (Top Pairs)", "Cụm kỹ năng (Clusters)"])
    
    with tab1:
        if df_pairs is not None:
            roles_p = df_pairs["role"].unique()
            r_sel = st.selectbox("Chọn vị trí cho Co-occurrence:", roles_p, key="cooc_role")
            sub_p = df_pairs[df_pairs["role"] == r_sel].sort_values(by="lift", ascending=False)
            st.dataframe(sub_p[["skill_a", "skill_b", "n_both", "p_b_given_a", "lift"]])
    
    with tab2:
        if df_clusters is not None:
            roles_c = df_clusters["role"].unique()
            r_c_sel = st.selectbox("Chọn vị trí xem Cụm:", roles_c, key="cluster_role")
            sub_c = df_clusters[df_clusters["role"] == r_c_sel]
            for idx, row in sub_c.iterrows():
                st.markdown(f"**Cụm #{row['cluster_id']}** (Số kỹ năng: {row['n_skills']}): `{row['skills']}`")