"""Bảng màu và giao diện chung (Streamlit + Plotly)."""
LIGHT, SAGE, GREEN, TEAL, DARK = "#E6E6E6", "#BAC8B1", "#7B9669", "#6C8480", "#404E3B"
# Thang liên tục (heatmap): nhạt -> đậm. Xếp theo độ sáng giảm dần nên đọc được cả khi in đen trắng.
SEQ = [[0.0, LIGHT], [0.35, SAGE], [0.7, GREEN], [1.0, DARK]]
# Chuỗi màu rời rạc: DARK và GREEN tách biệt về độ sáng; TEAL gần GREEN nên khi >3 nhóm hãy dùng thêm nhãn trực tiếp.
COLORWAY = [DARK, GREEN, TEAL, SAGE]

CSS = f"""
<style>
h1, h2, h3, h4 {{ color: {DARK}; }}
[data-testid="stMetric"] {{ background: {SAGE}; padding: 12px 16px; border-radius: 8px; }}
[data-testid="stMetricLabel"], [data-testid="stMetricValue"] {{ color: {DARK}; }}
.stTabs [aria-selected="true"] {{ color: {DARK}; }}
.note {{ border-left: 4px solid {GREEN}; background: {SAGE}55; padding: 8px 12px; border-radius: 4px; color: {DARK}; }}
</style>
"""


def apply(st):
    """Đăng ký template Plotly + CSS. Gọi 1 lần ở đầu app."""
    import plotly.graph_objects as go
    import plotly.io as pio
    pio.templates["skills"] = go.layout.Template(layout=go.Layout(
        font=dict(color=DARK), colorway=COLORWAY,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor=SAGE, linecolor=TEAL, zerolinecolor=TEAL),
        yaxis=dict(gridcolor=SAGE, linecolor=TEAL, zerolinecolor=TEAL),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        coloraxis=dict(colorscale=SEQ, colorbar=dict(outlinecolor=TEAL)),
    ), data=dict(bar=[go.Bar(marker=dict(line=dict(color=DARK, width=0.8)))]))  # viền tối: #7B9669/#BAC8B1 trên nền #E6E6E6 chỉ đạt tương phản 2.6/1.4
    pio.templates.default = "skills"
    st.markdown(CSS, unsafe_allow_html=True)
