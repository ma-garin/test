# App Function Inventory

ASTで関数一覧を抽出した読み取り専用レポート。secrets値は読んでいない。

- 対象ファイル数: 4
- 関数数: 460

| file | function | line | flags |
| --- | --- | --- | --- |
| 05.app\pmo_agent_app.py | `inject_css` | 130-4435 | search, prompt, source, streamlit |
| 05.app\pmo_agent_app.py | `format_duration_summary` | 4456-4465 | file_write |
| 05.app\pmo_agent_app.py | `save_index_map` | 4472-4473 | file_write |
| 05.app\pmo_agent_app.py | `count_chunks` | 4497-4507 | faiss, chunk, file_read |
| 05.app\pmo_agent_app.py | `faiss_index_metadata` | 4510-4539 | faiss, embedding, lexical, chunk, file_read |
| 05.app\pmo_agent_app.py | `count_list_entries` | 4549-4561 | file_read |
| 05.app\pmo_agent_app.py | `count_pdf_list_entries` | 4564-4565 | streamlit |
| 05.app\pmo_agent_app.py | `count_office_list_entries` | 4568-4569 | streamlit |
| 05.app\pmo_agent_app.py | `count_existing_active_pdfs` | 4582-4587 | source |
| 05.app\pmo_agent_app.py | `count_existing_active_rag_files` | 4590-4595 | source |
| 05.app\pmo_agent_app.py | `build_header` | 4598-4616 | chunk, streamlit |
| 05.app\pmo_agent_app.py | `render_breadcrumb` | 4619-4647 | search, streamlit |
| 05.app\pmo_agent_app.py | `set_document_status` | 4691-4700 | source, file_write |
| 05.app\pmo_agent_app.py | `save_uploaded_file` | 4703-4717 | file_read, file_write |
| 05.app\pmo_agent_app.py | `active_pdf_records` | 4720-4726 | source |
| 05.app\pmo_agent_app.py | `active_office_records` | 4729-4735 | source |
| 05.app\pmo_agent_app.py | `active_rag_records` | 4738-4744 | source |
| 05.app\pmo_agent_app.py | `split_existing_and_missing_pdfs` | 4747-4759 | source |
| 05.app\pmo_agent_app.py | `split_existing_and_missing_sources` | 4762-4774 | source |
| 05.app\pmo_agent_app.py | `update_missing_source_errors` | 4777-4788 | source, file_write |
| 05.app\pmo_agent_app.py | `update_missing_pdf_errors` | 4791-4792 | source |
| 05.app\pmo_agent_app.py | `write_list_file` | 4795-4805 | source, file_write |
| 05.app\pmo_agent_app.py | `write_active_source_lists` | 4808-4850 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `write_active_pdf_list` | 4853-4854 | source |
| 05.app\pmo_agent_app.py | `convert_active_documents_to_json` | 4868-4958 | source, streamlit, file_read, file_write |
| 05.app\pmo_agent_app.py | `selected_convertible_records` | 4961-4990 | source, file_write |
| 05.app\pmo_agent_app.py | `convert_selected_documents_to_json` | 4993-5105 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `reingest_selected_documents` | 5108-5122 | faiss, source |
| 05.app\pmo_agent_app.py | `rebuild_faiss_for_selected_documents` | 5129-5210 | faiss, source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `rebuild_faiss_from_json` | 5213-5265 | faiss, embedding, source, openai, streamlit, file_write |
| 05.app\pmo_agent_app.py | `rebuild_rag_index` | 5268-5282 | faiss |
| 05.app\pmo_agent_app.py | `app_environment` | 5289-5297 | secrets |
| 05.app\pmo_agent_app.py | `ollama_model_label` | 5320-5347 | faiss, embedding, ollama |
| 05.app\pmo_agent_app.py | `ollama_model_note` | 5350-5377 | faiss, embedding, ollama |
| 05.app\pmo_agent_app.py | `create_openai_client` | 5389-5399 | openai, secrets |
| 05.app\pmo_agent_app.py | `ollama_url` | 5402-5403 | ollama |
| 05.app\pmo_agent_app.py | `ollama_post` | 5406-5428 | ollama, streamlit, file_read |
| 05.app\pmo_agent_app.py | `ollama_get` | 5431-5446 | ollama, streamlit, file_read |
| 05.app\pmo_agent_app.py | `list_ollama_models` | 5449-5459 | ollama, file_write |
| 05.app\pmo_agent_app.py | `is_likely_embedding_model` | 5462-5474 | embedding |
| 05.app\pmo_agent_app.py | `ollama_generate_text` | 5477-5488 | prompt, ollama |
| 05.app\pmo_agent_app.py | `ollama_embed_texts` | 5491-5535 | embedding, prompt, openai, ollama, file_write |
| 05.app\pmo_agent_app.py | `generate_ai_text` | 5538-5552 | prompt, openai, ollama |
| 05.app\pmo_agent_app.py | `embed_texts` | 5555-5566 | embedding, openai, ollama |
| 05.app\pmo_agent_app.py | `tokenize_text` | 5569-5584 | search, file_write |
| 05.app\pmo_agent_app.py | `chunk_identifier` | 5587-5604 | chunk, source |
| 05.app\pmo_agent_app.py | `load_lexical_index` | 5607-5616 | faiss, lexical, file_read |
| 05.app\pmo_agent_app.py | `lexical_score_candidates` | 5619-5664 | lexical, chunk, score, file_write |
| 05.app\pmo_agent_app.py | `extract_json_object` | 5680-5714 | file_read |
| 05.app\pmo_agent_app.py | `unique_texts` | 5717-5738 | file_write |
| 05.app\pmo_agent_app.py | `generate_retrieval_queries` | 5741-5785 | prompt, file_write |
| 05.app\pmo_agent_app.py | `load_rag_resources` | 5789-5807 | faiss, embedding, chunk, source, file_read, secrets |
| 05.app\pmo_agent_app.py | `result_from_chunk` | 5810-5837 | chunk, source, score |
| 05.app\pmo_agent_app.py | `hybrid_search_chunks` | 5840-5945 | faiss, embedding, lexical, search, chunk, source, score, file_write |
| 05.app\pmo_agent_app.py | `rerank_results_with_llm` | 5948-6069 | rerank, chunk, prompt, score, file_write |
| 05.app\pmo_agent_app.py | `search_chunks` | 6072-6102 | rerank, search, chunk |
| 05.app\pmo_agent_app.py | `answer_question` | 6105-6169 | chunk, prompt, context |
| 05.app\pmo_agent_app.py | `answer_pmo_support` | 6172-6242 | chunk, prompt, context |
| 05.app\pmo_agent_app.py | `render_metric` | 6245-6254 | streamlit |
| 05.app\pmo_agent_app.py | `render_summary_card` | 6257-6267 | streamlit |
| 05.app\pmo_agent_app.py | `apply_pmo_prompt_template` | 6596-6608 | prompt, source, streamlit, session_state |
| 05.app\pmo_agent_app.py | `pmo_prompt_template_by_id` | 6611-6612 | prompt |
| 05.app\pmo_agent_app.py | `apply_pmo_agent` | 6619-6640 | prompt, source, streamlit, session_state |
| 05.app\pmo_agent_app.py | `handle_pmo_prompt_template_query` | 6643-6661 | prompt, streamlit |
| 05.app\pmo_agent_app.py | `handle_pmo_agent_query` | 6664-6682 | streamlit |
| 05.app\pmo_agent_app.py | `handle_pmo_screen_query` | 6685-6706 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `handle_rag_screen_query` | 6709-6733 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `handle_pmo_history_query` | 6736-6758 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_pmo_agent_grid` | 6761-6769 | streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_prompt_library` | 6772-6782 | prompt, streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_infinity_status_table` | 6785-6805 | streamlit, file_write |
| 05.app\pmo_agent_app.py | `set_pmo_screen` | 6879-6883 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `set_rag_screen` | 6886-6890 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `handle_main_nav_query` | 6893-6919 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_sidebar_nav_button` | 6922-6928 | streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_top_page` | 6931-6971 | streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_library_page` | 6974-6986 | prompt, streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_agents_page` | 6989-7000 | streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_coach_page` | 7003-7222 | rerank, search, chunk, prompt, source, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_pmo_artifacts_page` | 7225-7413 | streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `pmo_section` | 7416-7426 | streamlit |
| 05.app\pmo_agent_app.py | `pmo_chip_row` | 7429-7434 | streamlit |
| 05.app\pmo_agent_app.py | `pmo_task` | 7437-7445 | source |
| 05.app\pmo_agent_app.py | `pmo_element` | 7448-7456 | source |
| 05.app\pmo_agent_app.py | `pmo_source_basis` | 7467-7475 | source, file_write |
| 05.app\pmo_agent_app.py | `pmo_domain_recommendation` | 7478-7493 | source |
| 05.app\pmo_agent_app.py | `generate_pmo_recommendations` | 7496-7568 | prompt, source |
| 05.app\pmo_agent_app.py | `init_pmo_state` | 7571-7578 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `build_pmo_decision_pack` | 7581-7684 | chunk, source, score, file_write |
| 05.app\pmo_agent_app.py | `save_pmo_outcome` | 7687-7696 | file_write |
| 05.app\pmo_agent_app.py | `render_pmo_decision_pack` | 7708-7762 | source, score, streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_outcome_panel` | 7765-7788 | streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_pmo_source_grid` | 7791-7807 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_pmo_reference_findings` | 7810-7817 | streamlit |
| 05.app\pmo_agent_app.py | `render_pmo_check_item` | 7820-7837 | source, streamlit |
| 05.app\pmo_agent_app.py | `selected_pmo_items` | 7840-7851 | streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `evidence_location_label` | 7854-7869 | chunk, file_write |
| 05.app\pmo_agent_app.py | `generate_pmo_procedures` | 7872-7904 | streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_pmo_procedures` | 7907-7929 | streamlit |
| 05.app\pmo_agent_app.py | `build_pmo_deliverable` | 7932-8046 | chunk, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_section_intro` | 8059-8069 | streamlit |
| 05.app\pmo_agent_app.py | `render_documents_overview` | 8072-8108 | faiss, embedding, chunk, openai, streamlit |
| 05.app\pmo_agent_app.py | `render_flow_card` | 8111-8126 | streamlit |
| 05.app\pmo_agent_app.py | `render_ingestion_flow` | 8129-8168 | faiss, embedding, chunk, streamlit |
| 05.app\pmo_agent_app.py | `normalize_source` | 8171-8172 | source |
| 05.app\pmo_agent_app.py | `build_document_lookup` | 8175-8181 | source |
| 05.app\pmo_agent_app.py | `calc_sha256` | 8244-8251 | file_read |
| 05.app\pmo_agent_app.py | `read_file_bytes` | 8278-8280 | file_read |
| 05.app\pmo_agent_app.py | `read_template_registry` | 8289-8302 | file_read |
| 05.app\pmo_agent_app.py | `save_template_registry` | 8305-8309 | file_write |
| 05.app\pmo_agent_app.py | `extract_excel_template_metadata` | 8317-8372 | file_write |
| 05.app\pmo_agent_app.py | `template_search_text` | 8375-8389 | search, file_write |
| 05.app\pmo_agent_app.py | `template_keyword_parts` | 8392-8410 | file_write |
| 05.app\pmo_agent_app.py | `search_templates` | 8413-8415 | search |
| 05.app\pmo_agent_app.py | `search_template_records` | 8418-8441 | search, score, file_write |
| 05.app\pmo_agent_app.py | `general_template_records` | 8444-8495 | source |
| 05.app\pmo_agent_app.py | `template_prompt_context` | 8503-8532 | prompt, context, file_write |
| 05.app\pmo_agent_app.py | `save_uploaded_template_file` | 8535-8546 | file_read, file_write |
| 05.app\pmo_agent_app.py | `build_template_record` | 8549-8576 | source |
| 05.app\pmo_agent_app.py | `template_registry_rows` | 8592-8611 | file_write |
| 05.app\pmo_agent_app.py | `rag_results_summary` | 8628-8649 | chunk, file_write |
| 05.app\pmo_agent_app.py | `template_output_context` | 8663-8697 | search, context |
| 05.app\pmo_agent_app.py | `list_text` | 8710-8731 | file_write |
| 05.app\pmo_agent_app.py | `json_object_from_text` | 8734-8756 | search, file_read |
| 05.app\pmo_agent_app.py | `ai_template_sheet_outline` | 8768-8783 | file_write |
| 05.app\pmo_agent_app.py | `ai_template_mapping_outline` | 8786-8808 | file_write |
| 05.app\pmo_agent_app.py | `build_template_ai_prompt` | 8811-8864 | chunk, prompt, context, file_write |
| 05.app\pmo_agent_app.py | `enrich_template_context_with_ai` | 8867-8892 | prompt, context |
| 05.app\pmo_agent_app.py | `add_ai_input_sheet` | 8895-8929 | context |
| 05.app\pmo_agent_app.py | `fill_reference_sheet` | 8949-8986 | chunk, context, file_write |
| 05.app\pmo_agent_app.py | `build_general_template_workbook` | 8989-9075 | context |
| 05.app\pmo_agent_app.py | `template_custom_mapping_fields` | 9095-9114 | file_write |
| 05.app\pmo_agent_app.py | `template_mapping_field_options` | 9117-9126 | file_write |
| 05.app\pmo_agent_app.py | `empty_template_mapping` | 9129-9136 | file_write |
| 05.app\pmo_agent_app.py | `clean_template_mapping` | 9222-9271 | file_write |
| 05.app\pmo_agent_app.py | `template_mapping_to_editor_rows` | 9274-9301 | file_write |
| 05.app\pmo_agent_app.py | `editor_rows_to_template_mapping` | 9321-9372 | file_write |
| 05.app\pmo_agent_app.py | `update_template_registry_record` | 9375-9391 | file_write |
| 05.app\pmo_agent_app.py | `template_mapping_value_for_field` | 9394-9425 | context |
| 05.app\pmo_agent_app.py | `template_mapping_items_for_field` | 9428-9441 | context |
| 05.app\pmo_agent_app.py | `template_table_column_value` | 9458-9496 | context, source |
| 05.app\pmo_agent_app.py | `template_cell_warnings` | 9512-9532 | file_write |
| 05.app\pmo_agent_app.py | `dry_run_template_output_mapping` | 9535-9640 | chunk, context, source, file_write |
| 05.app\pmo_agent_app.py | `fallback_template_mapping_suggestions` | 9643-9680 | file_write |
| 05.app\pmo_agent_app.py | `build_template_mapping_ai_prompt` | 9683-9731 | prompt, file_write |
| 05.app\pmo_agent_app.py | `suggest_template_mapping_with_ai` | 9734-9792 | prompt, file_write |
| 05.app\pmo_agent_app.py | `merge_template_mapping_suggestion` | 9795-9820 | file_write |
| 05.app\pmo_agent_app.py | `template_output_mapping` | 9823-9851 | file_write |
| 05.app\pmo_agent_app.py | `add_ai_review_sheet` | 9854-9887 | context |
| 05.app\pmo_agent_app.py | `apply_template_output_mapping` | 9890-10027 | context, source, file_write |
| 05.app\pmo_agent_app.py | `generate_filled_template` | 10030-10128 | chunk, context, source, openai, file_write |
| 05.app\pmo_agent_app.py | `refresh_record_file_metadata` | 10131-10145 | source |
| 05.app\pmo_agent_app.py | `file_change_status` | 10148-10178 | source |
| 05.app\pmo_agent_app.py | `document_ingest_status` | 10181-10216 | faiss |
| 05.app\pmo_agent_app.py | `chunks_for_source` | 10229-10246 | faiss, chunk, source, file_read |
| 05.app\pmo_agent_app.py | `render_documents_table` | 10249-10300 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `set_operation_result` | 10303-10310 | streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `run_document_operation` | 10313-10318 | streamlit |
| 05.app\pmo_agent_app.py | `render_operation_result` | 10321-10336 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `record_key` | 10448-10450 | source |
| 05.app\pmo_agent_app.py | `file_exists` | 10453-10455 | source |
| 05.app\pmo_agent_app.py | `filter_document_records` | 10458-10499 | source, file_write |
| 05.app\pmo_agent_app.py | `render_document_filters` | 10502-10583 | streamlit |
| 05.app\pmo_agent_app.py | `render_doc_cell` | 10586-10590 | streamlit |
| 05.app\pmo_agent_app.py | `render_document_action_list` | 10593-10686 | source, streamlit |
| 05.app\pmo_agent_app.py | `registry_table_rows` | 10689-10710 | source, file_write |
| 05.app\pmo_agent_app.py | `save_registry_status_changes` | 10720-10772 | source, file_write |
| 05.app\pmo_agent_app.py | `selected_registry_sources` | 10775-10781 | source |
| 05.app\pmo_agent_app.py | `selected_delete_sources` | 10784-10785 | source |
| 05.app\pmo_agent_app.py | `bulk_update_registry_status` | 10788-10841 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `archived_file_path` | 10844-10860 | source |
| 05.app\pmo_agent_app.py | `delete_registry_files` | 10863-10921 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `restore_registry_files` | 10924-10986 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_document_detail_panel` | 10997-11052 | faiss, chunk, source, streamlit |
| 05.app\pmo_agent_app.py | `render_document_registry_data_table` | 11055-11177 | faiss, source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_template_detail` | 11180-11227 | source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_template_mapping_editor` | 11230-11469 | streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `save_draft` | 11371-11374 | file_write |
| 05.app\pmo_agent_app.py | `render_template_registry_page` | 11472-11579 | streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_template_output_waiting_panel` | 11582-11597 | openai, streamlit |
| 05.app\pmo_agent_app.py | `render_template_output_panel` | 11600-11780 | search, source, openai, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_template_output_expander` | 11783-11790 | streamlit |
| 05.app\pmo_agent_app.py | `render_template_output_waiting_expander` | 11793-11799 | streamlit |
| 05.app\pmo_agent_app.py | `format_result_title` | 11802-11816 | chunk, score |
| 05.app\pmo_agent_app.py | `result_meta_html` | 11819-11856 | lexical, rerank, chunk, source, score |
| 05.app\pmo_agent_app.py | `render_result_detail` | 11859-11861 | streamlit |
| 05.app\pmo_agent_app.py | `feedback_chunk_payload` | 11873-11896 | lexical, chunk, source, score, file_write |
| 05.app\pmo_agent_app.py | `save_feedback` | 11899-11927 | embedding, chunk, file_write |
| 05.app\pmo_agent_app.py | `save_rag_answer_history` | 11930-11983 | embedding, rerank, search, chunk, file_write |
| 05.app\pmo_agent_app.py | `render_rag_answer_history` | 11992-12053 | embedding, search, chunk, streamlit, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_short_balance_label` | 12084-12097 | search |
| 05.app\pmo_agent_app.py | `rag_chat_session_meta_label` | 12100-12111 | file_write |
| 05.app\pmo_agent_app.py | `ensure_rag_chat_state` | 12120-12124 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `new_rag_chat` | 12127-12130 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `save_rag_chat_session` | 12133-12178 | rerank, chunk, ollama, file_write |
| 05.app\pmo_agent_app.py | `apply_rag_chat_session_settings` | 12228-12262 | rerank, openai, ollama, streamlit, session_state |
| 05.app\pmo_agent_app.py | `load_rag_chat_session` | 12265-12277 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `update_rag_chat_session` | 12280-12297 | file_write |
| 05.app\pmo_agent_app.py | `duplicate_rag_chat_session` | 12300-12321 | file_write |
| 05.app\pmo_agent_app.py | `rag_chat_query_value` | 12324-12330 | streamlit |
| 05.app\pmo_agent_app.py | `clear_rag_chat_history_query_params` | 12333-12338 | streamlit |
| 05.app\pmo_agent_app.py | `handle_rag_chat_history_query` | 12341-12415 | search, streamlit, session_state |
| 05.app\pmo_agent_app.py | `save_pmo_chat_history` | 12418-12448 | embedding, search, chunk, file_write |
| 05.app\pmo_agent_app.py | `restore_pmo_chat_history` | 12457-12504 | chunk, source, streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_pmo_chat_history` | 12507-12536 | source, streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_feedback_form` | 12539-12585 | streamlit, file_write |
| 05.app\pmo_agent_app.py | `save_pmo_deliverable_draft` | 12588-12606 | file_write |
| 05.app\pmo_agent_app.py | `render_feedback_dashboard` | 12609-12853 | search, chunk, source, score, streamlit |
| 05.app\pmo_agent_app.py | `render_dashboard` | 12856-12890 | faiss, chunk, streamlit |
| 05.app\pmo_agent_app.py | `render_file_ingest_dialog` | 12894-12948 | faiss, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_documents` | 12951-13049 | faiss, embedding, source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_documents_v2` | 13052-13140 | faiss, embedding, streamlit |
| 05.app\pmo_agent_app.py | `rag_search_defaults` | 13143-13149 | rerank, search, secrets |
| 05.app\pmo_agent_app.py | `rag_knowledge_balance_by_value` | 13200-13217 | search |
| 05.app\pmo_agent_app.py | `rag_knowledge_balance_prompt` | 13220-13252 | prompt |
| 05.app\pmo_agent_app.py | `render_rag_knowledge_balance_control` | 13255-13285 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_search_precision_controls` | 13288-13320 | rerank, search, streamlit |
| 05.app\pmo_agent_app.py | `answer_model_choices_for_openai` | 13323-13324 | openai |
| 05.app\pmo_agent_app.py | `render_answer_model_runtime_controls` | 13327-13494 | faiss, embedding, openai, ollama, streamlit, secrets |
| 05.app\pmo_agent_app.py | `format_rag_chat_history` | 13497-13507 | file_write |
| 05.app\pmo_agent_app.py | `rag_chat_message_content_html` | 13510-13524 | file_write |
| 05.app\pmo_agent_app.py | `rag_chat_result_location` | 13527-13539 | file_write |
| 05.app\pmo_agent_app.py | `rag_chat_source_card_html` | 13542-13559 | source, score, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_reference_card_html` | 13562-13574 | score |
| 05.app\pmo_agent_app.py | `rag_chat_message_html` | 13577-13629 | search, source, file_write |
| 05.app\pmo_agent_app.py | `render_rag_chat_empty_workbench` | 13632-13651 | streamlit |
| 05.app\pmo_agent_app.py | `render_rag_chat_messages` | 13654-13662 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_rag_chat_reference_summary` | 13665-13696 | chunk, score, streamlit |
| 05.app\pmo_agent_app.py | `rag_chat_assistant_messages` | 13699-13713 | file_write |
| 05.app\pmo_agent_app.py | `render_rag_chat_header` | 13716-13750 | rerank, search, chunk, streamlit |
| 05.app\pmo_agent_app.py | `render_rag_chat_quick_replies` | 13753-13776 | prompt, streamlit |
| 05.app\pmo_agent_app.py | `render_rag_chat_template_output_panel` | 13810-13832 | streamlit |
| 05.app\pmo_agent_app.py | `render_rag_chat_reference_panel` | 13835-13964 | rerank, search, chunk, score, streamlit, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_history_groups` | 13967-14031 | search, file_write |
| 05.app\pmo_agent_app.py | `render_rag_chat_history_menu` | 14034-14081 | streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_rag_chat_history_row` | 14084-14121 | streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_rag_chat_history_panel` | 14124-14166 | search, streamlit, session_state |
| 05.app\pmo_agent_app.py | `process_rag_chat_prompt` | 14169-14208 | rerank, prompt, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `complete_rag_chat_pending` | 14211-14324 | rerank, search, chunk, prompt, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_rag_chat_native_styles` | 14327-15044 | source, streamlit |
| 05.app\pmo_agent_app.py | `render_rag_chat_native_header` | 15047-15077 | rerank, chunk, streamlit |
| 05.app\pmo_agent_app.py | `render_rag_chat_settings_panel` | 15080-15097 | rerank, search, streamlit |
| 05.app\pmo_agent_app.py | `render_rag_chat_mode` | 15100-15157 | rerank, prompt, streamlit, session_state |
| 05.app\pmo_agent_app.py | `rag_chat_beta_query_value` | 15160-15166 | streamlit |
| 05.app\pmo_agent_app.py | `clear_rag_chat_beta_query_params` | 15169-15174 | streamlit |
| 05.app\pmo_agent_app.py | `handle_rag_chat_beta_query` | 15190-15212 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `rag_chat_beta_history_panel_html` | 15232-15331 | search, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_beta_result_badge` | 15334-15343 | source, score |
| 05.app\pmo_agent_app.py | `rag_chat_beta_message_item_html` | 15346-15369 | source |
| 05.app\pmo_agent_app.py | `rag_chat_beta_thread_html` | 15372-15407 | score |
| 05.app\pmo_agent_app.py | `rag_chat_beta_reference_panel_html` | 15410-15502 | rerank, search, score, ollama, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_beta_center_header_html` | 15505-15522 | chunk |
| 05.app\pmo_agent_app.py | `clear_rag_chat_beta_rebuild_query_params` | 15525-15535 | search, prompt, streamlit, file_write |
| 05.app\pmo_agent_app.py | `handle_rag_chat_beta_rebuild_query` | 15547-15594 | rerank, search, prompt, streamlit, session_state |
| 05.app\pmo_agent_app.py | `rag_chat_beta_rebuild_history_html` | 15614-15703 | search, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_beta_rebuild_display_messages` | 15706-15736 | score |
| 05.app\pmo_agent_app.py | `rag_chat_beta_rebuild_message_html` | 15739-15764 | source, score |
| 05.app\pmo_agent_app.py | `rag_chat_beta_rebuild_chat_html` | 15767-15796 | prompt |
| 05.app\pmo_agent_app.py | `rag_chat_beta_rebuild_reference_html` | 15799-15880 | rerank, score, ollama, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_beta_rebuild_workspace_html` | 15883-16274 | rerank, search, source |
| 05.app\pmo_agent_app.py | `render_rag_chat_beta_mode` | 16277-16307 | rerank, streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_rag_chat_a_mode` | 16310-16312 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `rag_chat_document_default_results` | 16315-16345 | score |
| 05.app\pmo_agent_app.py | `rag_chat_document_context` | 16348-16363 | context |
| 05.app\pmo_agent_app.py | `rag_chat_document_browser_html` | 16366-16441 | search, context, score, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_document_message_html` | 16444-16469 | source, score |
| 05.app\pmo_agent_app.py | `rag_chat_document_chat_html` | 16472-16515 | rerank, search, prompt, context |
| 05.app\pmo_agent_app.py | `rag_chat_document_quotes_html` | 16518-16581 | rerank, search, context, score, file_write |
| 05.app\pmo_agent_app.py | `rag_chat_document_workspace_html` | 16584-17018 | rerank, search, source |
| 05.app\pmo_agent_app.py | `render_rag_chat_d_mode` | 17021-17052 | rerank, streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_rag_standard_search` | 17055-17290 | faiss, rerank, search, chunk, streamlit, session_state, file_write |
| 05.app\pmo_agent_app.py | `render_rag_search` | 17293-17319 | search, streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_pmo_support` | 17322-17345 | prompt, streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_agentic_status_rows` | 17358-17368 | streamlit |
| 05.app\pmo_agent_app.py | `render_agentic_top_page` | 17371-17459 | search, chunk, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_agentic_trace_page` | 17462-17516 | search, chunk, source, score, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_agentic_evaluation_page` | 17519-17550 | streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_agentic_harness_page` | 17553-17595 | rerank, search, streamlit |
| 05.app\pmo_agent_app.py | `render_agentic_settings_page` | 17598-17617 | streamlit |
| 05.app\pmo_agent_app.py | `render_agentic_rag_management` | 17620-17649 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_settings` | 17652-18003 | faiss, embedding, rerank, search, source, openai, ollama, streamlit, file_write, secrets |
| 05.app\pmo_agent_app.py | `is_admin_route` | 18006-18014 | streamlit |
| 05.app\pmo_agent_app.py | `admin_record_paths` | 18036-18057 | source, file_write |
| 05.app\pmo_agent_app.py | `admin_ingest_list_rows` | 18060-18101 | source, file_read, file_write |
| 05.app\pmo_agent_app.py | `admin_file_rows` | 18104-18126 | source, file_write |
| 05.app\pmo_agent_app.py | `remove_faiss_outputs` | 18144-18154 | faiss, lexical, chunk, file_write |
| 05.app\pmo_agent_app.py | `admin_permanently_delete_records` | 18157-18234 | faiss, source, streamlit, file_write |
| 05.app\pmo_agent_app.py | `render_admin_login` | 18237-18255 | streamlit, session_state |
| 05.app\pmo_agent_app.py | `render_admin_page` | 18258-18352 | source, streamlit |
| 05.app\pmo_agent_app.py | `main` | 18355-18517 | search, prompt, streamlit, session_state |
| 05.app\rag_web_min.py | `load_resources` | 16-42 | faiss, embedding, chunk, source, openai, streamlit, file_read, secrets |
| 05.app\rag_web_min.py | `search_chunks` | 44-78 | faiss, embedding, search, chunk, score, openai, file_write |
| 05.app\rag_web_min.py | `build_context` | 80-92 | context |
| 05.app\rag_web_min.py | `answer_question` | 94-114 | prompt, context, openai |
| eval\run_offline_retrieval_eval.py | `load_json` | 45-52 | file_read |
| eval\run_offline_retrieval_eval.py | `load_jsonl` | 55-74 | file_read, file_write |
| eval\run_offline_retrieval_eval.py | `as_chunk_list` | 77-88 | chunk |
| eval\run_offline_retrieval_eval.py | `normalize_source` | 95-97 | source |
| eval\run_offline_retrieval_eval.py | `source_basename` | 100-104 | source |
| eval\run_offline_retrieval_eval.py | `source_aliases` | 107-111 | source |
| eval\run_offline_retrieval_eval.py | `chunk_metadata` | 114-116 | chunk |
| eval\run_offline_retrieval_eval.py | `chunk_source` | 119-127 | chunk, source |
| eval\run_offline_retrieval_eval.py | `chunk_id` | 130-135 | chunk |
| eval\run_offline_retrieval_eval.py | `chunk_page` | 138-140 | chunk |
| eval\run_offline_retrieval_eval.py | `add_token_with_ngrams` | 173-182 | search |
| eval\run_offline_retrieval_eval.py | `chunk_source_aliases` | 218-228 | chunk, source |
| eval\run_offline_retrieval_eval.py | `corpus_source_candidates` | 231-235 | source |
| eval\run_offline_retrieval_eval.py | `expected_source_lookup` | 238-247 | source, file_write |
| eval\run_offline_retrieval_eval.py | `source_covered` | 250-251 | source |
| eval\run_offline_retrieval_eval.py | `source_recall` | 254-265 | source |
| eval\run_offline_retrieval_eval.py | `sorted_top_sources` | 268-277 | chunk, source, file_write |
| eval\run_offline_retrieval_eval.py | `build_text_cache` | 280-281 | chunk |
| eval\run_offline_retrieval_eval.py | `score_chunks` | 284-345 | lexical, chunk, source, score, file_write |
| eval\run_offline_retrieval_eval.py | `top_chunk_records` | 348-372 | chunk, source, score, file_write |
| eval\run_offline_retrieval_eval.py | `first_expected_rank` | 375-381 | chunk, source |
| eval\run_offline_retrieval_eval.py | `missing_at_10` | 384-399 | source, file_write |
| eval\run_offline_retrieval_eval.py | `chunk_recall` | 402-407 | chunk |
| eval\run_offline_retrieval_eval.py | `missing_expected_chunks_at_10` | 410-414 | chunk |
| eval\run_offline_retrieval_eval.py | `first_expected_chunk_rank` | 417-424 | chunk |
| eval\run_offline_retrieval_eval.py | `source_forced_records` | 427-460 | chunk, source, file_write |
| eval\run_offline_retrieval_eval.py | `forced_chunk_recall` | 463-472 | chunk, source |
| eval\run_offline_retrieval_eval.py | `forced_missing_expected_chunks_at_10` | 475-486 | chunk, source |
| eval\run_offline_retrieval_eval.py | `forced_first_expected_chunk_rank` | 489-501 | chunk, source |
| eval\run_offline_retrieval_eval.py | `evaluate_case` | 504-602 | lexical, chunk, source, score, file_write |
| eval\run_offline_retrieval_eval.py | `overall_summary` | 605-642 | chunk, source |
| eval\run_offline_retrieval_eval.py | `output_candidates` | 667-691 | file_write |
| eval\run_offline_retrieval_eval.py | `add` | 671-676 | file_write |
| eval\run_offline_retrieval_eval.py | `write_results_with_fallback` | 710-729 | file_write |
| eval\run_offline_retrieval_eval.py | `print_summary` | 732-782 | chunk, source |
| eval\run_offline_retrieval_eval.py | `parse_args` | 785-814 | lexical, chunk, source |
| eval\run_offline_retrieval_eval.py | `write_jsonl` | 817-846 | file_read, file_write |
| eval\run_offline_retrieval_eval.py | `main` | 873-906 | lexical, chunk, source |
| eval\analyze_retrieval_gaps.py | `load_json` | 34-41 | file_read |
| eval\analyze_retrieval_gaps.py | `load_jsonl` | 44-63 | file_read, file_write |
| eval\analyze_retrieval_gaps.py | `as_chunk_list` | 73-83 | chunk |
| eval\analyze_retrieval_gaps.py | `normalize_source` | 90-91 | source |
| eval\analyze_retrieval_gaps.py | `source_basename` | 94-98 | source |
| eval\analyze_retrieval_gaps.py | `source_aliases` | 101-104 | source |
| eval\analyze_retrieval_gaps.py | `chunk_metadata` | 107-109 | chunk |
| eval\analyze_retrieval_gaps.py | `chunk_id` | 112-114 | chunk |
| eval\analyze_retrieval_gaps.py | `chunk_source` | 117-125 | chunk, source |
| eval\analyze_retrieval_gaps.py | `chunk_page` | 128-130 | chunk |
| eval\analyze_retrieval_gaps.py | `chunk_text` | 133-135 | chunk |
| eval\analyze_retrieval_gaps.py | `query_terms` | 143-161 | file_write |
| eval\analyze_retrieval_gaps.py | `expected_source_for_chunk` | 185-192 | chunk, source |
| eval\analyze_retrieval_gaps.py | `top_ids` | 195-209 | chunk |
| eval\analyze_retrieval_gaps.py | `top_sources` | 212-220 | chunk, source, file_write |
| eval\analyze_retrieval_gaps.py | `chunk_rank` | 223-231 | chunk |
| eval\analyze_retrieval_gaps.py | `matched_query_terms` | 234-240 | chunk, file_write |
| eval\analyze_retrieval_gaps.py | `classify_reason` | 243-273 | lexical, chunk, source |
| eval\analyze_retrieval_gaps.py | `suggested_action_for` | 276-291 | lexical, chunk, source |
| eval\analyze_retrieval_gaps.py | `build_analysis` | 294-378 | lexical, chunk, source, file_write |
| eval\analyze_retrieval_gaps.py | `metric_summary` | 385-429 | chunk, source |
| eval\analyze_retrieval_gaps.py | `write_gap_report` | 436-545 | rerank, search, chunk, source, file_write |
| eval\analyze_retrieval_gaps.py | `write_improvement_report` | 548-660 | lexical, rerank, search, chunk, source, score, file_write |
| eval\analyze_retrieval_gaps.py | `write_source_forced_report` | 663-756 | chunk, source, file_write |
| eval\analyze_retrieval_gaps.py | `main` | 759-796 | lexical, chunk, source |
