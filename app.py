import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import shutil
import datetime

import sqlite3

# --- Constants & Configuration ---
MASTER_DB_FILE = 'agency.db'
BACKUP_DB_FILE = 'agency.db.bak'
CSV_LEGACY_FILE = 'agency_master_db.csv' # For migration

# The core numeric metrics we track
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

# The categorical/key columns
KEY_COLUMNS = ['Day', 'Client_Name', 'Campaign_Name', 'Campaign_Type']

REQUIRED_COLUMNS = KEY_COLUMNS + CORE_METRICS

# --- Data Access Layer ---

def get_connection():
    """Returns a connection to the SQLite DB."""
    return sqlite3.connect(MASTER_DB_FILE)

def init_db():
    """
    Initializes the SQLite DB.
    Creates table if not exists.
    Migrates legacy CSV data if DB is empty and CSV exists.
    """
    conn = get_connection()
    c = conn.cursor()
    
    # Create Table with Composite Primary Key
    # preventing duplicates for the same Day+Client+Campaign
    c.execute('''
        CREATE TABLE IF NOT EXISTS agency_performance (
            Day DATE,
            Client_Name TEXT,
            Campaign_Name TEXT,
            Campaign_Type TEXT,
            Connections_Sent INTEGER DEFAULT 0,
            Connections_Accepted INTEGER DEFAULT 0,
            Messages_Sent INTEGER DEFAULT 0,
            Message_Replies INTEGER DEFAULT 0,
            Interested INTEGER DEFAULT 0,
            Not_Interested INTEGER DEFAULT 0,
            Maybe INTEGER DEFAULT 0,
            Follow_Ups_Sent INTEGER DEFAULT 0,
            PRIMARY KEY (Day, Client_Name, Campaign_Name)
        )
    ''')
    conn.commit()
    
    # Check if DB is empty to trigger migration
    c.execute('SELECT count(*) FROM agency_performance')
    count = c.fetchone()[0]
    
    if count == 0 and os.path.exists(CSV_LEGACY_FILE):
        try:
            st.info("Migrating legacy CSV data to SQLite database...")
            df = pd.read_csv(CSV_LEGACY_FILE)
            
            # Ensure types
            if 'Day' in df.columns:
                df['Day'] = pd.to_datetime(df['Day']).dt.date
            
            # Clean Metrics (NaN -> 0)
            for col in CORE_METRICS:
                if col in df.columns:
                    df[col] = df[col].fillna(0).astype(int)
                else:
                    df[col] = 0
            
            # Insert data
            # strict=False allows ignoring extra columns in CSV if any, 
            # but usually we want to map. pd.to_sql is easiest for bulk load.
            # We use if_exists='append', but since table is empty it's fine.
            df.to_sql('agency_performance', conn, if_exists='append', index=False)
            st.success("Migration complete!")
        except Exception as e:
            st.error(f"Migration failed: {e}")
            
    conn.close()

def create_backup():
    """Creates a backup of the current DB file."""
    if os.path.exists(MASTER_DB_FILE):
        shutil.copy(MASTER_DB_FILE, BACKUP_DB_FILE)
        return True
    return False

def restore_backup():
    """Restores the DB from the backup file."""
    if os.path.exists(BACKUP_DB_FILE):
        # Close any potential open connections (though Streamlit threading makes this tricky, 
        # usually FS copy works fine for SQLite if not rigidly locked)
        try:
            shutil.copy(BACKUP_DB_FILE, MASTER_DB_FILE)
            return True, "Database restored from last backup."
        except Exception as e:
            return False, f"Restore failed: {e}"
    return False, "No backup found."

def load_data():
    """Loads the master DB data into a DataFrame."""
    if not os.path.exists(MASTER_DB_FILE):
        init_db()
    
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT * FROM agency_performance", conn)
        conn.close()
        
        if 'Day' in df.columns:
            df['Day'] = pd.to_datetime(df['Day'])
            
        return df
    except Exception as e:
        st.error(f"Error loading database: {e}")
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

def save_data(new_df, create_bak=True):
    """
    Saves new data to the master DB using Upsert (INSERT OR REPLACE).
    Composite Key: Day, Client_Name, Campaign_Name
    """
    try:
        if create_bak:
            create_backup()
            
        # Ensure data types for SQL
        new_df = new_df.copy()
        new_df['Day'] = pd.to_datetime(new_df['Day']).dt.date.astype(str) # SQLite stores dates as strings/text usually
        
        for col in CORE_METRICS:
            if col not in new_df.columns:
                new_df[col] = 0
            new_df[col] = new_df[col].fillna(0).astype(int)
            
        for col in ['Client_Name', 'Campaign_Name', 'Campaign_Type']:
            new_df[col] = new_df[col].fillna('Unknown').astype(str)
            
        conn = get_connection()
        c = conn.cursor()
        
        # Upsert Loop
        # We perform INSERT OR REPLACE to overwrite existing rows with same key
        # SQLite uses "INSERT OR REPLACE INTO table (col1,...) VALUES (?,...)"
        
        cols = KEY_COLUMNS + CORE_METRICS
        placeholders = ', '.join(['?'] * len(cols))
        query = f'''
            INSERT OR REPLACE INTO agency_performance 
            ({', '.join(cols)}) 
            VALUES ({placeholders})
        '''
        
        data_to_insert = new_df[cols].to_records(index=False).tolist()
        
        c.executemany(query, data_to_insert)
        conn.commit()
        conn.close()
        
        return True, "Data successfully saved to Master DB (SQLite)."
    except Exception as e:
        return False, f"Error saving data: {str(e)}"

def delete_clients(clients_to_delete):
    """Deletes all records for specific clients."""
    try:
        create_backup()
        conn = get_connection()
        c = conn.cursor()
        
        # Parameterized query for safety
        placeholders = ', '.join(['?'] * len(clients_to_delete))
        query = f"DELETE FROM agency_performance WHERE Client_Name IN ({placeholders})"
        
        c.execute(query, clients_to_delete)
        deleted_count = c.rowcount
        conn.commit()
        conn.close()
        
        return True, f"Deleted {deleted_count} records for clients: {', '.join(clients_to_delete)}"
    except Exception as e:
        return False, f"Error deleting records: {str(e)}"

# --- Main Application ---
def main():
    st.set_page_config(page_title="NinjAI Dashboard", layout="wide")
    
    # --- Sidebar: Logo & Data Management ---
    if os.path.exists('ninjai_logo_v1.png'):
        st.sidebar.image('ninjai_logo_v1.png', use_container_width=True)
    
    st.sidebar.header("Data Management")
    
    st.title("NinjAI Dashboard")

    init_db()
    
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
            
            # Mapping State
            mapping = {}
            
            # Create dropdowns for each required column
            st.sidebar.markdown("### Key Fields")
            mapping['Day'] = st.sidebar.selectbox("Day (Date)", raw_cols, index=0 if 'Day' in raw_cols else 0)
            mapping['Client_Name'] = st.sidebar.selectbox("Client Name", raw_cols, index=raw_cols.index('Client_Name') if 'Client_Name' in raw_cols else 0)
            mapping['Campaign_Name'] = st.sidebar.selectbox("Campaign Name", raw_cols, index=raw_cols.index('Campaign_Name') if 'Campaign_Name' in raw_cols else 0)
            mapping['Campaign_Type'] = st.sidebar.selectbox("Campaign Type", raw_cols, index=raw_cols.index('Campaign_Type') if 'Campaign_Type' in raw_cols else 0)
            
            st.sidebar.markdown("### Metrics")
            
            # Special option for missing columns
            NA_OPTION = "<Not Available (Fill 0)>"
            
            for metric in CORE_METRICS:
                # Build options
                options = [NA_OPTION] + raw_cols
                
                # Default selection logic
                default_idx = 0 
                if metric in raw_cols:
                    default_idx = options.index(metric)
                    
                mapping[metric] = st.sidebar.selectbox(f"{metric}", options, index=default_idx)
            
            if st.sidebar.button("Process & Preview Data"):
                # Apply Mapping
                processed_data = {}
                
                # 1. Handle Day - Direct Map
                processed_data['Day'] = raw_df[mapping['Day']].values
                
                # 2. Handle Key Fields - Direct Map
                for field in ['Client_Name', 'Campaign_Name', 'Campaign_Type']:
                    col_name = mapping[field]
                    processed_data[field] = raw_df[col_name].values

                # 3. Handle Metrics - Map or Fill 0
                for metric in CORE_METRICS:
                    col_name = mapping[metric]
                    if col_name == NA_OPTION:
                        processed_data[metric] = [0] * len(raw_df)
                    else:
                        processed_data[metric] = raw_df[col_name].values
                
                new_df = pd.DataFrame(processed_data)
                
                # --- Data Cleaning ---
                new_df['Day'] = pd.to_datetime(new_df['Day'], errors='coerce')
                if new_df['Day'].isnull().any():
                     st.warning(f"Dropped {new_df['Day'].isnull().sum()} rows due to invalid dates.")
                     new_df.dropna(subset=['Day'], inplace=True)
                
                for col in ['Client_Name', 'Campaign_Name', 'Campaign_Type']:
                     new_df[col] = new_df[col].fillna('Unknown').astype(str)

                for col in CORE_METRICS:
                    new_df[col] = pd.to_numeric(new_df[col], errors='coerce').fillna(0).astype(int)
                
                st.session_state['preview_df'] = new_df
                st.success("Data processed! Review below.")
                
        except Exception as e:
            st.sidebar.error(f"Error processing upload: {e}")


    # --- Sidebar: Danger Zone ---
    st.sidebar.divider()
    st.sidebar.markdown("### ⚠️ Data Correction / Utilities")
    
    # Undo Logic
    if st.sidebar.button("↩️ Undo Last Import"):
        success, msg = restore_backup()
        if success:
            st.sidebar.success(msg)
            # Ensure reload happens to refresh views
            st.rerun()
        else:
            st.sidebar.error(msg)
            
    # Delete Logic
    df_for_delete = load_data()
    if not df_for_delete.empty:
        all_clients_del = sorted(df_for_delete['Client_Name'].unique().tolist())
        clients_to_delete = st.sidebar.multiselect("Select Clients to DELETE (Permanent)", all_clients_del)
        
        if clients_to_delete:
            if st.sidebar.button("🗑️ Delete Selected Clients", type="primary"):
                 success, msg = delete_clients(clients_to_delete)
                 if success:
                     st.sidebar.success(msg)
                     st.rerun()
                 else:
                     st.sidebar.error(msg)

    # --- Main Area ---
    
    # 1. New Data Review Section (Conditional)
    if 'preview_df' in st.session_state and uploaded_file:
        st.subheader("📝 Review & Edit New Data")
        with st.expander("Expand to review and edit data", expanded=True):
            edited_df = st.data_editor(
                st.session_state['preview_df'], 
                num_rows="dynamic",
                key="data_editor"
            )
            
            col1, col2 = st.columns([1, 4])
            with col1:
                if st.button("💾 Save to Master DB", type="primary"):
                    success, msg = save_data(edited_df)
                    if success:
                        st.success(msg)
                        # Clear session state and increment uploader key to reset UI
                        del st.session_state['preview_df']
                        st.session_state['uploader_key'] += 1
                        st.rerun()
                    else:
                        st.error(msg)
            with col2:
                if st.button("Cancel"):
                     del st.session_state['preview_df']
                     st.rerun()
        st.divider()

    # 2. Main Dashboard
    
    # Load Master Data
    df = load_data()
    
    if df.empty:
        st.info("The Master Database is empty. Upload data via the sidebar to see the dashboard.")
    else:
        # --- Sidebar Filter ---
        st.sidebar.markdown("### Dashboard Filters")
        all_clients = sorted(df['Client_Name'].unique().tolist())
        selected_client = st.sidebar.selectbox("Filter by Client", ["All Clients"] + all_clients)
        
        # Default view is the full dataset or client subset
        if selected_client != "All Clients":
            client_df = df[df['Client_Name'] == selected_client]
            
            # Hierarchical Campaign Filter
            available_campaigns = sorted(client_df['Campaign_Name'].unique().tolist())
            selected_campaign = st.sidebar.selectbox("Filter by Campaign", ["All Campaigns"] + available_campaigns)
            
            if selected_campaign != "All Campaigns":
                dashboard_df = client_df[client_df['Campaign_Name'] == selected_campaign].copy()
                st.header(f"Dashboard: {selected_client} - {selected_campaign}")
            else:
                dashboard_df = client_df.copy()
                st.header(f"Dashboard: {selected_client} (All Campaigns)")
        else:
            dashboard_df = df.copy()
            st.header("Dashboard: All Clients")
            
        # --- Date Filter ---
        st.sidebar.markdown("### Date Filter")
        date_options = ["All Time", "Last 7 Days", "Last 30 Days", "Last 60 Days", "Last 365 Days", "Custom Range"]
        selected_date_range = st.sidebar.selectbox("Select Period", date_options)
        
        start_date = None
        end_date = None
        today = datetime.date.today()
        
        if selected_date_range == "Last 7 Days":
            start_date = today - datetime.timedelta(days=7)
            end_date = today
        elif selected_date_range == "Last 30 Days":
            start_date = today - datetime.timedelta(days=30)
            end_date = today
        elif selected_date_range == "Last 60 Days":
            start_date = today - datetime.timedelta(days=60)
            end_date = today
        elif selected_date_range == "Last 365 Days":
            start_date = today - datetime.timedelta(days=365)
            end_date = today
        elif selected_date_range == "Custom Range":
            col1, col2 = st.sidebar.columns(2)
            start_date = col1.date_input("Start Date", value=today - datetime.timedelta(days=30))
            end_date = col2.date_input("End Date", value=today)
            
        # Apply Date Filter if set
        if start_date and end_date:
            # Ensure filtering works on the date part
            mask = (dashboard_df['Day'].dt.date >= start_date) & (dashboard_df['Day'].dt.date <= end_date)
            dashboard_df = dashboard_df.loc[mask]
            st.caption(f"Showing data from **{start_date}** to **{end_date}**")
            
        # Global KPI Cards
        # "Total Outreach" = Connections Sent + Messages Sent
        # "Total Interested" = Interested (filtered by selection)
        # "Aggregate Reply Rate" = Total Replies / Total Messages Sent
        
        kpi1, kpi2, kpi3 = st.columns(3)
        
        total_connections = dashboard_df['Connections_Sent'].sum()
        total_messages = dashboard_df['Messages_Sent'].sum()
        total_replies = dashboard_df['Message_Replies'].sum()
        
        total_outreach = total_connections + total_messages
        total_interested = dashboard_df['Interested'].sum()
        
        reply_rate = 0
        if total_messages > 0:
            reply_rate = (total_replies / total_messages) * 100
            
        kpi1.metric("Total Outreach", f"{total_outreach:,}")
        kpi2.metric("Total Interested", f"{total_interested:,}")
        kpi3.metric("Aggregate Reply Rate", f"{reply_rate:.1f}%")
        
        # --- Trend Chart V2 ---
        st.subheader("📈 Performance Trends")
        
        # Metric Selector
        default_metrics = ['Connections_Sent', 'Messages_Sent', 'Interested']
        # Validating defaults exist in CORE_METRICS
        default_metrics = [m for m in default_metrics if m in CORE_METRICS]
        
        selected_metrics = st.multiselect(
            "Select Metrics to Visualize:",
            options=CORE_METRICS,
            default=default_metrics
        )
        
        if selected_metrics:
            # Aggregating by Day for the chart based on current filtered view
            # We explicitly sum only the selected numeric columns
            chart_df = dashboard_df.groupby('Day')[selected_metrics].sum().reset_index()
            
            # Melt for Plotly
            melted_df = chart_df.melt(id_vars=['Day'], value_vars=selected_metrics, var_name='Metric', value_name='Count')
            
            fig = px.line(
                melted_df, 
                x='Day', 
                y='Count', 
                color='Metric',
                markers=True,
                title="Daily Performance Metrics",
            )
            fig.update_layout(height=450)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Please select at least one metric to visualize.")        

        # Raw Data View
        st.subheader("Raw Data")
        with st.expander("View Raw Master DB Data"):
            st.dataframe(df)

        # Raw Data Download
        st.divider()
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Master DB (CSV)",
            data=csv,
            file_name='agency_master_db_export.csv',
            mime='text/csv',
        )

if __name__ == "__main__":
    main()
