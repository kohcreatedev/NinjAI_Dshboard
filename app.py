import streamlit as st
import pandas as pd
import plotly.express as px
import datetime

# --- Constants & Configuration ---
# We keep these to ensure the app knows which columns to expect
CORE_METRICS = [
    'Connections_Sent',
    'Connections_Accepted',
    'Messages_Sent',
    'Message_Replies',
    'Interested',
    'Not_Interested',
    'Maybe',
    'Follow_Ups_Sent'
]
KEY_COLUMNS = ['Day', 'Client_Name', 'Campaign_Name', 'Campaign_Type']
REQUIRED_COLUMNS = KEY_COLUMNS + CORE_METRICS

# --- Data Access Layer (Neon PostgreSQL) ---

def get_db_connection():
    """Uses the native Streamlit connection to Neon."""
    return st.connection("postgresql", type="sql")

def load_data():
    """Loads all data from the Neon database."""
    conn = get_db_connection()
    try:
        # st.connection handles the engine and connection for us
        df = conn.query("SELECT * FROM agency_performance", ttl=0) # ttl=0 ensures fresh data
        
        if not df.empty and 'day' in df.columns:
            # SQL often returns lowercase column names; we standardize them back
            df.columns = [c.title() if c.lower() in [k.lower() for k in KEY_COLUMNS] else c for c in df.columns]
            df['Day'] = pd.to_datetime(df['Day'])
        return df
    except Exception as e:
        # If table doesn't exist yet or is empty
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

def save_data(new_df):
    """Saves data to Neon using the connection engine."""
    conn = get_db_connection()
    try:
        # Ensure data types are clean before sending to SQL
        save_df = new_df.copy()
        save_df['Day'] = pd.to_datetime(save_df['Day']).dt.date
        
        # We use the underlying SQLAlchemy engine from the connection
        with conn.engine.begin() as engine_conn:
            save_df.to_sql('agency_performance', engine_conn, if_exists='append', index=False)
        
        return True, "Data successfully synced to Neon Cloud!"
    except Exception as e:
        return False, f"Error saving to database: {str(e)}"

def delete_clients(clients_to_delete):
    """Deletes records from Neon for specific clients."""
    conn = get_db_connection()
    try:
        # Using a session to execute a manual delete command
        with conn.session as s:
            for client in clients_to_delete:
                s.execute(
                    "DELETE FROM agency_performance WHERE client_name = :name", 
                    {"name": client}
                )
            s.commit()
        return True, f"Deleted records for: {', '.join(clients_to_delete)}"
    except Exception as e:
        return False, f"Delete failed: {e}"

# --- Main Application ---
def main():
    st.set_page_config(page_title="NinjAI Dashboard", layout="wide")
    
    st.sidebar.header("Data Management")
    st.title("NinjAI Dashboard (Cloud Enabled)")

    # Initialize uploader key for reset
    if 'uploader_key' not in st.session_state:
        st.session_state['uploader_key'] = 0

    uploaded_file = st.sidebar.file_uploader(
        "Upload New Data (CSV)", 
        type=["csv"], 
        key=f"uploader_{st.session_state['uploader_key']}"
    )

    if uploaded_file:
        st.sidebar.subheader("Map Columns")
        try:
            raw_df = pd.read_csv(uploaded_file)
            raw_cols = raw_df.columns.tolist()
            mapping = {}
            
            mapping['Day'] = st.sidebar.selectbox("Day (Date)", raw_cols)
            mapping['Client_Name'] = st.sidebar.selectbox("Client Name", raw_cols)
            mapping['Campaign_Name'] = st.sidebar.selectbox("Campaign Name", raw_cols)
            mapping['Campaign_Type'] = st.sidebar.selectbox("Campaign Type", raw_cols)
            
            NA_OPTION = "<Not Available (Fill 0)>"
            for metric in CORE_METRICS:
                options = [NA_OPTION] + raw_cols
                mapping[metric] = st.sidebar.selectbox(f"{metric}", options)
            
            if st.sidebar.button("Process & Preview"):
                processed_data = {field: raw_df[mapping[field]].values for field in KEY_COLUMNS}
                for metric in CORE_METRICS:
                    processed_data[metric] = [0]*len(raw_df) if mapping[metric] == NA_OPTION else raw_df[mapping[metric]].values
                
                new_df = pd.DataFrame(processed_data)
                new_df['Day'] = pd.to_datetime(new_df['Day'], errors='coerce')
                new_df.dropna(subset=['Day'], inplace=True)
                
                for col in CORE_METRICS:
                    new_df[col] = pd.to_numeric(new_df[col]).fillna(0).astype(int)
                
                st.session_state['preview_df'] = new_df
        except Exception as e:
            st.sidebar.error(f"Error: {e}")

    # --- Data Review & Saving ---
    if 'preview_df' in st.session_state:
        st.subheader("📝 Review New Data")
        edited_df = st.data_editor(st.session_state['preview_df'], num_rows="dynamic")
        if st.button("💾 Save to Neon Database", type="primary"):
            success, msg = save_data(edited_df)
            if success:
                st.success(msg)
                del st.session_state['preview_df']
                st.session_state['uploader_key'] += 1
                st.rerun()

    # --- Dashboard View ---
    df = load_data()
    
    if df.empty:
        st.info("Database is empty. Upload data to begin.")
    else:
        # Sidebar Filters
        st.sidebar.divider()
        all_clients = sorted(df['Client_Name'].unique().tolist())
        selected_client = st.sidebar.selectbox("Filter by Client", ["All Clients"] + all_clients)
        
        dashboard_df = df.copy()
        if selected_client != "All Clients":
            dashboard_df = dashboard_df[dashboard_df['Client_Name'] == selected_client]

        # KPI Cards
        kpi1, kpi2, kpi3 = st.columns(3)
        total_outreach = dashboard_df['Connections_Sent'].sum() + dashboard_df['Messages_Sent'].sum()
        kpi1.metric("Total Outreach", f"{total_outreach:,}")
        kpi2.metric("Interested", f"{dashboard_df['Interested'].sum():,}")
        
        # Trend Chart
        st.subheader("📈 Performance Trends")
        selected_metrics = st.multiselect("Metrics:", CORE_METRICS, default=['Connections_Sent', 'Interested'])
        if selected_metrics:
            chart_df = dashboard_df.groupby('Day')[selected_metrics].sum().reset_index()
            fig = px.line(chart_df, x='Day', y=selected_metrics, markers=True)
            st.plotly_chart(fig, use_container_width=True)

        # Danger Zone
        st.sidebar.markdown("### ⚠️ Danger Zone")
        clients_to_del = st.sidebar.multiselect("Delete Clients:", all_clients)
        if clients_to_del and st.sidebar.button("Permanently Delete", type="primary"):
            delete_clients(clients_to_del)
            st.rerun()

if __name__ == "__main__":
    main()
