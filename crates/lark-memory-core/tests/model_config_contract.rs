use std::path::PathBuf;

use lark_memory_core::model_config::ModelRegistry;

#[test]
fn loads_existing_models_json_example_with_serving_policy() {
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let path = repo_root.join("models.json.example");

    let registry = ModelRegistry::from_file(&path).expect("models.json.example loads");

    let base = registry
        .get("deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B")
        .expect("base model exists");
    assert_eq!(base.serving.prompt_style, "buddy_deepseek_r1");
    assert_eq!(base.serving.api_mode, "both");
    assert_eq!(base.serving.request_timeout_ms, 120000);
    assert!(!base.tool.cli_path.trim().is_empty());

    let tuned = registry
        .get("lark-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice")
        .expect("tuned model exists");
    assert_eq!(tuned.serving.default_max_tokens, 128);
    assert_eq!(tuned.serving.max_input_chars, 32768);
    assert_eq!(tuned.serving.request_timeout_ms, 300000);
}
