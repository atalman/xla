#include "torch_xla/csrc/ops/cumsum.h"

#include "torch/csrc/lazy/core/tensor_util.h"
#include "torch_xla/csrc/convert_ops.h"
#include "torch_xla/csrc/helpers.h"
#include "torch_xla/csrc/lowering_context.h"
#include "torch_xla/csrc/ops/infer_output_shape.h"
#include "torch_xla/csrc/reduction.h"
#include "torch_xla/csrc/tensor_util.h"
#include "torch_xla/csrc/torch_util.h"

namespace torch_xla {
namespace ir {
namespace ops {
namespace {

xla::XlaOp LowerCumSum(xla::XlaOp input, xla::int64_t dim,
                       c10::optional<at::ScalarType> dtype) {
  xla::XlaOp casted_input = CastToScalarType(input, dtype);
  const xla::Shape& input_shape = XlaHelpers::ShapeOfXlaOp(casted_input);
  xla::XlaOp init = XlaHelpers::ScalarValue<float>(
      0, input_shape.element_type(), casted_input.builder());
  xla::XlaComputation reducer =
      XlaHelpers::CreateAddComputation(input_shape.element_type());
  return BuildCumulativeComputation(casted_input, dim, reducer, init);
}

xla::Shape NodeOutputShape(const Value& input,
                           c10::optional<at::ScalarType> dtype) {
  if (dtype) {
    return xla::ShapeUtil::ChangeElementType(
        input.shape(), MakeXlaPrimitiveType(*dtype, /*device=*/nullptr));
  }
  return input.shape();
}

}  // namespace

CumSum::CumSum(const Value& input, xla::int64_t dim,
               c10::optional<at::ScalarType> dtype)
    : Node(ir::OpKind(at::aten::cumsum), {input},
           [&]() { return NodeOutputShape(input, dtype); },
           /*num_outputs=*/1,
           torch::lazy::MHash(dim, torch::lazy::OptionalOr<int>(dtype, -1))),
      dim_(dim),
      dtype_(dtype) {}

NodePtr CumSum::Clone(OpList operands) const {
  return MakeNode<CumSum>(operands.at(0), dim_, dtype_);
}

XlaOpVector CumSum::Lower(LoweringContext* loctx) const {
  xla::XlaOp input = loctx->GetOutputOp(operand(0));
  return ReturnOp(LowerCumSum(input, dim_, dtype_), loctx);
}

std::string CumSum::ToString() const {
  std::stringstream ss;
  ss << Node::ToString() << ", dim=" << dim_;
  if (dtype_) {
    ss << ", dtype=" << *dtype_;
  }
  return ss.str();
}

}  // namespace ops
}  // namespace ir
}  // namespace torch_xla
