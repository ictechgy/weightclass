# Homebrew formula — weightclass
#
# 이 파일이 ictechgy/homebrew-tap 의 Formula/weightclass.rb 원본이다.
# tap 을 직접 고치지 말고 여기서 고쳐 복사한다. 갱신 절차는 RELEASING.md 참고.
class Weightclass < Formula
  include Language::Python::Virtualenv

  desc "Local, policy-driven routing for agent CLI workflows"
  homepage "https://github.com/ictechgy/weightclass"
  url "https://files.pythonhosted.org/packages/4d/4f/d82ff86e1fc2d9d23b7c03dba0a5aa5bf2a61b00458c571a24433e759e4f/weightclass-0.12.0.tar.gz"
  sha256 "d65eb74c2d9a26f8001bab736f5bae8accc753f328a76479d16eae86e472828d"
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

    # 잘못된 입력은 닫히는 방향으로 실패하고 태스크를 되비추지 않아야 한다.
    output = pipe_output("#{bin}/wclass classify 2>&1", "", 2)
    assert_equal '{"error": "invalid_task"}', output.strip
  end
end
