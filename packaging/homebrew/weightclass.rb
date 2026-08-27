# Homebrew formula — weightclass
#
# 이 파일이 ictechgy/homebrew-tap 의 Formula/weightclass.rb 원본이다.
# tap 을 직접 고치지 말고 여기서 고쳐 복사한다. 갱신 절차는 RELEASING.md 참고.
class Weightclass < Formula
  include Language::Python::Virtualenv

  desc "Local, policy-driven routing for agent CLI workflows"
  homepage "https://github.com/ictechgy/weightclass"
  url "https://files.pythonhosted.org/packages/b2/b3/e9d011103d7dde79614064359d34effb724babe32842a66387d328365624/weightclass-0.17.9.tar.gz"
  sha256 "634dcbe9f66934f15262611115ca3e7dace51712cb9883fd1e1254f6f6d19a75"
  license "MIT"

  depends_on "python@3.13"

  # 런타임 의존성이 없으므로 resource 블록이 없다. 그래도 virtualenv 로 설치해
  # 사용자의 시스템 site-packages 를 건드리지 않는다.
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/wclass --version")

    # 분류가 결정적이라는 것이 이 도구의 핵심 계약이므로 그것을 검사한다.
    assert_equal '{"tier": "low"}',
                 pipe_output("#{bin}/wclass classify", "Fix a spelling typo.", 0).strip
    assert_equal '{"tier": "high"}',
                 pipe_output("#{bin}/wclass classify", "Review the authorization boundary.", 0).strip
    assert_match '"schema_version": 1',
                 shell_output("#{bin}/wclass example-policy claude-cost-focused")
    assert_match "wclass-advisory", shell_output("#{bin}/wclass-advisory --help")
    assert_match "--confirm-task-egress", shell_output("#{bin}/wclass-advisory run --help")
    assert_match "usage: wclass-advisory prune", shell_output("#{bin}/wclass-advisory prune --help")
    assert_match "usage: wclass-advisory install-skill",
                 shell_output("#{bin}/wclass-advisory install-skill --help")
    managed_root = testpath/"advisory-v1"
    system bin/"wclass-advisory", "init", "--state-root", managed_root,
           "--vendor", "codex",
           "--model", "cheap=cheap", "--model", "advisor=advisor",
           "--model", "expensive=expensive",
           "--effort", "cheap=low", "--effort", "advisor=high",
           "--effort", "expensive=high"
    assert_match '"campaign_ready":true',
                 shell_output("#{bin}/wclass-advisory doctor --state-root #{managed_root} " \
                              "--vendor codex --workflow all")
    assert_match "usage: wclass-advisory cli-check",
                 shell_output("#{bin}/wclass-advisory cli-check --help")
    assert_match "--confirm-provider-egress",
                 shell_output("#{bin}/wclass-advisory provider-check --help")

    # 잘못된 입력은 닫히는 방향으로 실패하고 태스크를 되비추지 않아야 한다.
    output = pipe_output("#{bin}/wclass classify 2>&1", "", 2)
    assert_equal '{"error": "invalid_task"}', output.strip
  end
end
