#include "torch_xla/csrc/ops/collective_permute.h"

#include "absl/strings/str_join.h"
#include "tensorflow/compiler/xla/shape_util.h"
#include "torch_xla/csrc/lowering_context.h"
#include "torch_xla/csrc/ops/infer_output_shape.h"
#include "torch_xla/csrc/ops/xla_ops.h"

namespace torch_xla {
namespace ir {
namespace ops {
namespace {

xla::Shape NodeOutputShape(
    const Value& input, const Value& token,
    const std::vector<std::pair<xla::int64_t, xla::int64_t>>&
        source_target_pairs) {
  auto shape_fn = [&](absl::Span<const xla::XlaOp> operands) -> xla::XlaOp {
    CollectivePermuteResult result =
        BuildCollectivePermute(operands[0], operands[1], source_target_pairs);
    return xla::Tuple(operands[0].builder(), {result.result, result.token});
  };
  return InferOutputShape({input.shape(), token.shape()}, shape_fn);
}

}  // namespace

CollectivePermute::CollectivePermute(
    const Value& input, const Value& token,
    std::vector<std::pair<xla::int64_t, xla::int64_t>> source_target_pairs)
    : Node(xla_collective_permute, {input, token},
           [&]() { return NodeOutputShape(input, token, source_target_pairs); },
           /*num_outputs=*/2, torch::lazy::MHash(source_target_pairs)),
      source_target_pairs_(std::move(source_target_pairs)) {}

NodePtr CollectivePermute::Clone(OpList operands) const {
  return MakeNode<CollectivePermute>(operands.at(0), operands.at(1),
                                     source_target_pairs_);
}

XlaOpVector CollectivePermute::Lower(LoweringContext* loctx) const {
  xla::XlaOp input = loctx->GetOutputOp(operand(0));
  xla::XlaOp token = loctx->GetOutputOp(operand(1));
  CollectivePermuteResult result =
      BuildCollectivePermute(input, token, source_target_pairs_);
  return ReturnOps({result.result, result.token}, loctx);
}

std::string CollectivePermute::ToString() const {
  std::stringstream ss;
  ss << Node::ToString() << ", source_target_pairs=(";
  for (size_t i = 0; i < source_target_pairs_.size(); ++i) {
    ss << (i == 0 ? "(" : ", (");
    ss << source_target_pairs_[i].first << ", "
       << source_target_pairs_[i].second << ")";
  }
  ss << ")";
  return ss.str();
}

}  // namespace ops
}  // namespace ir
}  // namespace torch_xla
