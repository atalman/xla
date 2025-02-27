#pragma once

#include "torch_xla/csrc/ir.h"

namespace torch_xla {
namespace ir {
namespace ops {

class MaxInDim : public Node {
 public:
  MaxInDim(const Value& input, xla::int64_t dim, bool keepdim);

  std::string ToString() const override;

  NodePtr Clone(OpList operands) const override;

  XlaOpVector Lower(LoweringContext* loctx) const override;

  xla::int64_t dim() const { return dim_; };

  bool keepdim() const { return keepdim_; }

 private:
  xla::int64_t dim_;
  bool keepdim_;
};

}  // namespace ops
}  // namespace ir
}  // namespace torch_xla
